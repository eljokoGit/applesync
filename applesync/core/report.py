"""Rapport final d'exécution, lisible et nominatif.

Un rapport par exécution, en Markdown, écrit dans `.applesync/rapports/`.
Les écarts et incidents sont listés PAR NOM — jamais un pourcentage seul.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from applesync.core.copier import CopyResult
from applesync.core.inventory import Inventory
from applesync.core.planner import SyncPlan
from applesync.core.verifier import VerificationReport


def fmt_bytes(n: int) -> str:
    """Format lisible (unités binaires) — partagé avec l'UI."""
    return _fmt_bytes(n)


def fmt_duration(s: float) -> str:
    return _fmt_duration(s)


def _fmt_bytes(n: int) -> str:
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if n < 1024 or unit == "Tio":
            return f"{n:.1f} {unit}" if unit != "o" else f"{n} o"
        n /= 1024
    return f"{n} o"


def _fmt_duration(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else (f"{m}m{sec:02d}s" if m else f"{sec}s")


@dataclass
class RunReport:
    run_id: str
    device_label: str
    status: str = "en cours"          # terminé | interrompu | échec
    inventory: Optional[Inventory] = None
    plan: Optional[SyncPlan] = None
    copies: list[CopyResult] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)   # (path, erreur)
    # (source iPhone, rangé sous, identique à) — organisation « archive »
    duplicates_routed: list[tuple[str, str, str]] = field(default_factory=list)
    verification: Optional[VerificationReport] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    REPORTS_RELPATH = Path(".applesync") / "rapports"

    def to_markdown(self) -> str:
        lines: list[str] = []
        add = lines.append
        add(f"# Rapport de synchronisation — {self.run_id}")
        add("")
        add(f"- **Appareil** : {self.device_label}")
        add(f"- **Début** : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started_at))}")
        if self.finished_at:
            add(f"- **Fin** : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.finished_at))}"
                f" (durée {_fmt_duration(self.finished_at - self.started_at)})")
        add(f"- **Statut** : **{self.status.upper()}**")
        if self.error:
            add(f"- **Erreur** : {self.error}")
        add("")

        if self.inventory is not None:
            inv = self.inventory
            add("## Inventaire source")
            add("")
            add(f"- {inv.count} fichiers, {_fmt_bytes(inv.total_bytes)}")
            add(f"- Double énumération : {'oui — concordante' if inv.double_checked else 'NON'}")
            add(f"- Empreinte : `{inv.fingerprint()}`")
            add(f"- Durée : {_fmt_duration(inv.duration_s)}")
            add("")

        if self.plan is not None:
            p = self.plan
            add("## Plan")
            add("")
            add(f"- À copier : {len(p.to_copy)} ({_fmt_bytes(sum(f.size for f in p.to_copy))})")
            add(f"- Déjà synchronisés : {len(p.already_synced)}")
            add(f"- Adoptés (déjà sur disque, ré-enregistrés) : {len(p.to_adopt)}")
            add(f"- Conflits (copiés sous nom versionné) : {len(p.conflicts)}")
            for c in p.conflicts:
                add(f"  - `{c.remote.path}` → `{c.versioned_path}` — {c.reason}")
            add(f"- Disparus de l'iPhone (conservés sur PC) : {len(p.missing_on_device)}")
            for e in p.missing_on_device:
                add(f"  - `{e.source_path}` (copié le "
                    f"{time.strftime('%Y-%m-%d', time.localtime(e.synced_at))}, "
                    f"local : `{e.local_path}`)")
            add("")

        add("## Copies")
        add("")
        copied_bytes = sum(c.remote.size for c in self.copies)
        resumed = [c for c in self.copies if c.resumed_from > 0]
        add(f"- Fichiers copiés : {len(self.copies)} ({_fmt_bytes(copied_bytes)})")
        if resumed:
            add(f"- Dont reprises en cours de fichier : {len(resumed)}")
            for c in resumed:
                add(f"  - `{c.remote.path}` repris à l'octet {c.resumed_from}")
        if self.duplicates_routed:
            add(f"- Doublons de contenu rangés sous `_Doublons/` : "
                f"{len(self.duplicates_routed)}")
            for src, dup_rel, original in self.duplicates_routed:
                add(f"  - `{src}` → `{dup_rel}` (identique à `{original}`)")
        if self.failures:
            add(f"- **Échecs : {len(self.failures)}**")
            for path, err in self.failures:
                add(f"  - `{path}` : {err}")
        add("")

        add("## Vérification de la destination")
        add("")
        if self.verification is None:
            add("- **NON EFFECTUÉE** — cette exécution ne certifie rien.")
        else:
            v = self.verification
            add(f"- Fichiers contrôlés : {v.checked_count}")
            add(f"- Relus et hachés : {v.hashed_count}")
            add(f"- Conformes : {v.ok_count}")
            if v.ok:
                add("- **Aucun écart.**")
            else:
                add(f"- **ÉCARTS : {len(v.discrepancies)}** — liste nominative :")
                for d in v.discrepancies:
                    add(f"  - `{d.source_path}` [{d.kind}] {d.detail}")
        add("")
        return "\n".join(lines)

    def save(self, dest_root: Path) -> Path:
        out_dir = Path(dest_root) / self.REPORTS_RELPATH
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"rapport_{self.run_id}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path
