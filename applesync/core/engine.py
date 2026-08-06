"""Moteur d'exécution : orchestre inventaire → plan → copie → vérification.

Utilisé tel quel par l'UI (dans un thread de travail) et par les tests de bout
en bout. Deux étapes séparées volontairement :

1. `prepare()` : inventaire (double énumération) + plan. Rien n'est écrit.
   Le résultat est présenté à l'utilisateur pour VALIDATION.
2. `execute(prepared)` : copie + vérification + rapport. Ne démarre jamais
   sans un `PreparedRun` issu de `prepare()`.

Toute erreur d'inventaire arrête tout : jamais de copie sur inventaire
douteux. Une déconnexion en cours de copie interrompt l'exécution en laissant
un état repris à l'identique au prochain lancement (fichiers .part).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from applesync.core.copier import CopyCancelled, CopyResult, copy_file
from applesync.core.inventory import Inventory, take_inventory
from applesync.core.journal import Journal, new_run_id
from applesync.core.layout import Layout, LayoutLockedError, MirrorLayout
from applesync.core.manifest import Manifest
from applesync.core.planner import SyncPlan, build_plan
from applesync.core.report import RunReport
from applesync.core.verifier import VerificationReport, verify_against_inventory
from applesync.device.base import (
    DeviceBackend,
    DeviceDisconnectedError,
    DeviceError,
    RemoteFile,
)


@dataclass
class PreparedRun:
    """Résultat de la phase 1, à présenter pour validation avant toute copie."""

    inventory: Inventory
    plan: SyncPlan
    device_label: str
    udid: str
    breakdown_csv: Optional[Path] = None   # ventilation mois × extension


@dataclass
class ProgressSnapshot:
    """État instantané de la copie, pour l'UI (fichier, compteur, volume, débit, ETA)."""

    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    bytes_per_s: float = 0.0

    @property
    def eta_s(self) -> Optional[float]:
        if self.bytes_per_s <= 0:
            return None
        return (self.bytes_total - self.bytes_done) / self.bytes_per_s


ProgressListener = Callable[[ProgressSnapshot], None]
PhaseListener = Callable[[str], None]


class SyncEngine:
    def __init__(self, backend: DeviceBackend, dest_root: Path,
                 layout: Optional[Layout] = None):
        self.backend = backend
        self.dest_root = Path(dest_root)
        self.layout = layout or MirrorLayout()

    # ------------------------------------------------------------------ phase 1
    def prepare(
        self,
        udid: str,
        phase_cb: Optional[PhaseListener] = None,
        inventory_progress: Optional[Callable[[int, str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> PreparedRun:
        """Inventaire vérifié + plan. Lève au moindre doute.

        N'écrit rien dans le miroir de sauvegarde ; exporte en revanche la
        ventilation mois × extension de l'inventaire en CSV dans
        `.applesync/rapports/` (utile en mode inventaire seul)."""
        if phase_cb:
            phase_cb("Connexion à l'appareil…")
        session = self.backend.connect(udid)
        try:
            info = session.device_info()
            label = f"{info.name} ({info.model}, iOS {info.ios_version}, {info.udid})"
            if phase_cb:
                phase_cb("Inventaire (double énumération)…")
            inventory = take_inventory(
                session, progress_cb=inventory_progress, cancel=cancel
            )
        finally:
            session.close()

        if phase_cb:
            phase_cb("Calcul du plan…")
        with Manifest(self.dest_root) as manifest:
            locked = manifest.locked_layout()
            if locked is not None and locked != self.layout.id:
                raise LayoutLockedError(locked, self.layout.id)
            plan = build_plan(inventory, manifest, self.dest_root, self.layout)

        from applesync.core.analyze import write_breakdown_csv
        from applesync.core.report import RunReport

        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(inventory.taken_at))
        csv_path = write_breakdown_csv(
            inventory,
            self.dest_root / RunReport.REPORTS_RELPATH / f"inventaire_{ts}.csv",
        )
        return PreparedRun(
            inventory=inventory, plan=plan, device_label=label, udid=udid,
            breakdown_csv=csv_path,
        )

    # ------------------------------------------------------------------ placement
    def _finalize_placement(self, remote, result, manifest, journal, report,
                            ts_map, run_assigned) -> str:
        """Décide et applique l'emplacement définitif d'un fichier copié.

        Un seul déplacement atomique couvre : datation définitive (EXIF, si
        l'organisation le demande) puis rangement des doublons de contenu.
        Collisions résolues en .~N contre le disque ET les cibles déjà
        promises dans ce run. Jamais d'écrasement."""
        from applesync.core.planner import versioned_target

        provisional = result.local_relpath
        final_rel = provisional

        if self.layout.finalize_dating:
            final_rel = self._dated_rel(remote, provisional, ts_map)

        prior = None
        from applesync.core.layout import SHARED_DIRNAME

        est_partage = final_rel.startswith(SHARED_DIRNAME + "/")
        if self.layout.duplicates_dir is not None and not est_partage:
            # Les albums partagés gardent leur structure par album : un
            # contenu identique à la photothèque n'y est pas un « doublon »
            # à ranger, c'est la même photo vue par le partage.
            prior = manifest.lookup_by_content(result.sha256, remote.size)
            if prior is not None:
                final_rel = f"{self.layout.duplicates_dir}/{final_rel}"

        if final_rel != provisional:
            candidate = final_rel
            if (self.dest_root / candidate).exists() or candidate in run_assigned:
                candidate = versioned_target(self.dest_root, final_rel, run_assigned)
            dst = self.dest_root / candidate
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.dest_root / provisional, dst)
            final_rel = candidate
        run_assigned.add(final_rel)

        if prior is not None:
            journal.event(
                "doublon_range",
                path=remote.path,
                range_sous=final_rel,
                identique_a=prior.local_path,
                sha256=result.sha256,
            )
            report.duplicates_routed.append(
                (remote.path, final_rel, prior.local_path)
            )
        elif final_rel != provisional:
            journal.event("place_definitivement", path=remote.path, cible=final_rel)

        result.local_relpath = final_rel
        return final_rel

    def _dated_rel(self, remote: RemoteFile, provisional: str, ts_map: dict) -> str:
        """Cible datée définitive : EXIF de la photo locale, mtime en repli ;
        les MOV de Live Photos et les AAE héritent de la date de leur photo."""
        from applesync.core.exifdate import exif_timestamp
        from applesync.core.layout import PHOTO_EXTENSIONS, _dir_stem, shared_target

        path = remote.path
        part = shared_target(path)
        if part is not None:
            return part          # albums partagés : à part, structure conservée
        ext = path.rsplit(".", 1)[-1].upper() if "." in path else ""
        layout = self.layout

        if ext in PHOTO_EXTENSIONS:
            ts = exif_timestamp(self.dest_root / provisional) or remote.mtime
            ts_map[_dir_stem(path)] = ts
            return layout.dated_target(remote, ts, as_live=False)

        pair = layout.paired_photo(remote) if ext in ("MOV", "AAE") else None
        if pair is not None:
            ts = ts_map.get(_dir_stem(pair.path), pair.mtime)
            return layout.dated_target(remote, ts, as_live=(ext == "MOV"))

        return layout.dated_target(remote, remote.mtime, as_live=False)

    # ------------------------------------------------------------------ phase 2
    def execute(
        self,
        prepared: PreparedRun,
        progress: Optional[ProgressListener] = None,
        phase_cb: Optional[PhaseListener] = None,
        cancel: Optional[Callable[[], bool]] = None,
        deep_verify: bool = True,
        verify_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> RunReport:
        """Copie le plan validé, vérifie la destination, écrit rapport+journal.

        Retourne un RunReport quel que soit le dénouement (terminé, interrompu,
        échec) — le statut et l'erreur y figurent. Ne lève que sur bug interne.
        """
        run_id = new_run_id()
        report = RunReport(run_id=run_id, device_label=prepared.device_label)
        report.inventory = prepared.inventory
        report.plan = prepared.plan

        journal = Journal(self.dest_root, run_id)
        manifest = Manifest(self.dest_root)
        manifest.set_meta("layout", self.layout.id)   # fige l'organisation
        manifest.start_run(run_id, prepared.inventory.device_udid)
        journal.event(
            "debut_execution",
            appareil=prepared.device_label,
            inventaire_fichiers=prepared.inventory.count,
            inventaire_octets=prepared.inventory.total_bytes,
            empreinte=prepared.inventory.fingerprint(),
            a_copier=len(prepared.plan.to_copy),
            conflits=len(prepared.plan.conflicts),
            disparus=len(prepared.plan.missing_on_device),
        )
        manifest.update_run(
            run_id,
            inventory_count=prepared.inventory.count,
            inventory_bytes=prepared.inventory.total_bytes,
        )

        transfers = prepared.plan.files_to_transfer
        if self.layout.finalize_dating:
            # Les .AAE suivent la date de leur photo : ils passent en dernier,
            # une fois toutes les photos datées (EXIF lu à la copie).
            transfers = sorted(
                transfers,
                key=lambda t: (t[0].path.upper().endswith(".AAE"), t[0].path),
            )
        ts_map: dict[tuple[str, str], int] = {}   # (dossier, nom) → date retenue
        run_assigned: set[str] = set()            # cibles finales promises ce run
        snap = ProgressSnapshot(
            files_total=len(transfers),
            bytes_total=sum(f.size for f, _ in transfers),
        )
        window_start = time.time()
        window_bytes = 0

        session = None
        status = "terminé"
        try:
            # --- adoption : fichiers déjà sur disque, jamais re-copiés -------
            if prepared.plan.to_adopt:
                if phase_cb:
                    phase_cb(f"Adoption de {len(prepared.plan.to_adopt)} fichiers déjà présents…")
                from applesync.core.verifier import _sha256_of

                for adopt_i, f in enumerate(prepared.plan.to_adopt, 1):
                    if cancel and cancel():
                        raise CopyCancelled("adoption interrompue")
                    rel = prepared.plan.targets[f.path]
                    if verify_progress:
                        verify_progress(adopt_i, len(prepared.plan.to_adopt), rel)
                    sha = _sha256_of(self.dest_root / rel)
                    manifest.record_file(
                        f, sha, rel, run_id, prepared.inventory.device_udid
                    )
                    journal.event("fichier_adopte", path=f.path, sha256=sha)

            # --- copie -------------------------------------------------------
            if transfers:
                if phase_cb:
                    phase_cb("Connexion pour la copie…")
                session = self.backend.connect(prepared.udid)
                if phase_cb:
                    phase_cb("Copie…")

            for remote, target_rel in transfers:
                snap.current_file = remote.path

                def file_progress(done: int, total: int, _remote=remote) -> None:
                    nonlocal window_start, window_bytes
                    base = snap.bytes_done
                    now = time.time()
                    elapsed = now - window_start
                    if elapsed >= 1.0:
                        snap.bytes_per_s = ((base + done) - window_bytes) / elapsed
                        window_start = now
                        window_bytes = base + done
                    if progress:
                        current = ProgressSnapshot(
                            current_file=_remote.path,
                            files_done=snap.files_done,
                            files_total=snap.files_total,
                            bytes_done=base + done,
                            bytes_total=snap.bytes_total,
                            bytes_per_s=snap.bytes_per_s,
                        )
                        progress(current)

                if self.layout.finalize_dating:
                    # Fichier de transit orphelin (interruption entre copie et
                    # placement) : jamais enregistré au manifeste → on repart.
                    stale = self.dest_root / target_rel
                    if stale.exists():
                        journal.event("transit_orphelin_purge", cible=target_rel)
                        stale.unlink()

                try:
                    result = copy_file(
                        session,
                        remote,
                        self.dest_root,
                        target_rel,
                        journal,
                        cancel=cancel,
                        progress_cb=file_progress,
                    )
                except DeviceDisconnectedError as e:
                    journal.event("deconnexion", path=remote.path, erreur=str(e))
                    report.failures.append((remote.path, f"déconnexion : {e}"))
                    status = "interrompu"
                    report.error = (
                        f"Session coupée pendant {remote.path} (écran verrouillé ou "
                        f"câble débranché). Les fichiers déjà copiés sont acquis ; "
                        f"le fichier en cours reprendra à l'octet près au prochain "
                        f"lancement."
                    )
                    break
                except DeviceError as e:
                    journal.event("echec_fichier", path=remote.path, erreur=str(e))
                    report.failures.append((remote.path, str(e)))
                    # Erreur limitée à un fichier : on continue, elle restera
                    # visible au rapport et la vérification la re-signalera.
                    continue

                final_rel = self._finalize_placement(
                    remote, result, manifest, journal, report, ts_map, run_assigned
                )
                report.copies.append(result)
                manifest.record_file(
                    remote,
                    result.sha256,
                    final_rel,
                    run_id,
                    prepared.inventory.device_udid,
                )
                snap.files_done += 1
                snap.bytes_done += remote.size
                if progress:
                    progress(snap)

        except CopyCancelled:
            status = "interrompu"
            report.error = (
                "Interruption demandée. Les fichiers déjà copiés sont acquis ; "
                "le fichier en cours reprendra à l'octet près."
            )
            journal.event("interruption_utilisateur")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

        # --- vérification --------------------------------------------------
        # On vérifie ce qui est censé être sur disque : tout l'inventaire si
        # l'exécution est allée au bout, sinon uniquement ce qui a été copié
        # ou adopté cette fois (les .part ne comptent jamais comme copiés).
        try:
            if status == "terminé":
                if phase_cb:
                    phase_cb("Vérification complète de la destination…")
                to_check: list[RemoteFile] = list(prepared.inventory.files)
            else:
                if phase_cb:
                    phase_cb("Vérification des fichiers copiés avant interruption…")
                copied_paths = {c.remote.path for c in report.copies}
                adopted = {f.path for f in prepared.plan.to_adopt}
                to_check = [
                    f for f in prepared.inventory.files
                    if f.path in copied_paths or f.path in adopted
                ]
            report.verification = verify_against_inventory(
                to_check,
                manifest,
                self.dest_root,
                deep_hash=deep_verify,
                progress_cb=verify_progress,
                cancel=None,   # la vérification d'un run ne s'interrompt pas
            )
            journal.event(
                "verification",
                controles=report.verification.checked_count,
                haches=report.verification.hashed_count,
                conformes=report.verification.ok_count,
                ecarts=[
                    {"source": d.source_path, "type": d.kind, "detail": d.detail}
                    for d in report.verification.discrepancies
                ],
            )
            if status == "terminé" and not report.verification.ok:
                status = "échec"
                report.error = (
                    f"Vérification en écart sur "
                    f"{len(report.verification.discrepancies)} fichier(s) — "
                    f"voir la liste nominative. NE PAS supprimer les originaux."
                )
            if status == "terminé" and report.failures:
                status = "échec"
                report.error = (
                    f"{len(report.failures)} fichier(s) en échec de copie — "
                    f"voir la liste. NE PAS supprimer les originaux."
                )
        except Exception as e:  # la vérification ne doit jamais passer sous silence
            status = "échec"
            report.error = f"Vérification impossible : {e}"
            journal.event("verification_impossible", erreur=str(e))

        report.status = status
        report.finished_at = time.time()
        journal.event(
            "fin_execution",
            statut=status,
            copies=len(report.copies),
            echecs=len(report.failures),
        )
        report_path = report.save(self.dest_root)
        manifest.update_run(
            run_id,
            finished_at=report.finished_at,
            status={"terminé": "completed", "interrompu": "interrupted", "échec": "failed"}[status],
            copied_count=len(report.copies),
            copied_bytes=sum(c.remote.size for c in report.copies),
            report_path=str(report_path),
        )
        manifest.close()
        journal.close()
        return report
