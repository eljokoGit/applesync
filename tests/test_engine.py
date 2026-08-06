"""Bout en bout sur simulateur : préparer → valider → exécuter → vérifier."""

import pytest

from applesync.core.engine import SyncEngine
from applesync.core.inventory import InventoryMismatchError
from applesync.core.journal import read_journal
from applesync.core.manifest import Manifest
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_premier_run_complet(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    assert prepared.inventory.count == len(backend.tree)
    assert len(prepared.plan.to_copy) == prepared.inventory.count

    report = engine.execute(prepared, deep_verify=True)
    assert report.status == "terminé"
    assert len(report.copies) == prepared.inventory.count
    assert report.verification is not None and report.verification.ok
    assert report.verification.hashed_count == prepared.inventory.count

    # Rapport et journal écrits et relisibles
    rapport = dest / ".applesync" / "rapports" / f"rapport_{report.run_id}.md"
    assert rapport.exists()
    contenu = rapport.read_text(encoding="utf-8")
    assert "TERMINÉ" in contenu and "Aucun écart" in contenu
    journaux = list((dest / ".applesync" / "logs").glob("*.jsonl"))
    assert journaux
    events = read_journal(journaux[0])
    kinds = [e["event"] for e in events]
    assert kinds[0] == "debut_execution" and kinds[-1] == "fin_execution"


def test_second_run_idempotent(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))

    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy
    assert len(prepared2.plan.already_synced) == prepared2.inventory.count
    report2 = engine.execute(prepared2)
    assert report2.status == "terminé"
    assert not report2.copies
    assert report2.verification.ok


def test_increment_quelques_nouveaux(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))
    backend.add_file("202312_a/IMG_90001.HEIC", 7000, 1_701_000_000)
    backend.add_file("202312_a/IMG_90002.MOV", 90_000, 1_701_000_060)

    prepared = engine.prepare(backend.INFO.udid)
    assert len(prepared.plan.to_copy) == 2
    report = engine.execute(prepared)
    assert report.status == "terminé"
    assert len(report.copies) == 2


def test_inventaire_tronque_bloque_avant_toute_copie(dest):
    faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=10)
    backend = SimulatedBackend(SimProfile.small(), faults)
    engine = SyncEngine(backend, dest)
    with pytest.raises(InventoryMismatchError):
        engine.prepare(backend.INFO.udid)
    # Rien n'a été écrit dans la destination
    assert not any(p for p in dest.rglob("*.HEIC"))
    assert not any(p for p in dest.rglob("*.part"))


def test_deconnexion_pendant_copie_puis_reprise(dest):
    """Écran verrouillé au milieu de la copie : run interrompu, fichiers déjà
    copiés acquis et vérifiés, reprise au run suivant sans repartir de zéro."""
    prof = SimProfile.small()
    probe = SimulatedBackend(prof)
    ordered = sorted(probe.tree, key=lambda f: f.path)
    victime = ordered[10]
    faults = FaultPlan(
        fail_read_path=victime.path,
        fail_read_at_byte=victime.size // 2,
        fail_read_as_disconnect=True,
    )
    backend = SimulatedBackend(prof, faults)
    engine = SyncEngine(backend, dest)

    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "interrompu"
    assert report.error and "reprendra à l'octet près" in report.error
    assert len(report.copies) == 10                    # les 10 d'avant la victime
    assert report.verification.ok                      # ce qui est copié est sain
    assert (dest / (victime.path + ".part")).exists()  # partiel conservé
    assert not (dest / victime.path).exists()          # jamais de partiel déguisé

    # Run suivant sans panne : reprise, pas de re-copie des 10 premiers
    backend_ok = SimulatedBackend(prof)
    engine2 = SyncEngine(backend_ok, dest)
    prepared2 = engine2.prepare(backend_ok.INFO.udid)
    assert len(prepared2.plan.already_synced) == 10
    report2 = engine2.execute(prepared2)
    assert report2.status == "terminé"
    repris = [c for c in report2.copies if c.remote.path == victime.path]
    assert len(repris) == 1 and repris[0].resumed_from == victime.size // 2
    assert report2.verification.ok
    assert report2.verification.hashed_count == prepared2.inventory.count


def test_progression_verification_remontee(backend, dest):
    """La vérification remonte (n_faits, n_total, fichier) jusqu'au bout —
    c'est ce qui alimente la barre de progression de l'UI."""
    engine = SyncEngine(backend, dest)
    calls = []
    report = engine.execute(
        engine.prepare(backend.INFO.udid),
        verify_progress=lambda i, n, p: calls.append((i, n, p)),
    )
    assert report.status == "terminé"
    assert calls, "aucune progression remontée"
    derniers = calls[-1]
    assert derniers[0] == derniers[1] == report.verification.checked_count
    assert all(n == derniers[1] for _, n, _ in calls)
    assert all(p for _, _, p in calls)   # le fichier courant est toujours nommé


def test_suppression_cote_iphone_conservee_et_signalee(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))
    disparu = backend.tree[0]
    backend.remove_file(disparu.path)

    prepared = engine.prepare(backend.INFO.udid)
    assert [e.source_path for e in prepared.plan.missing_on_device] == [disparu.path]
    report = engine.execute(prepared)
    assert report.status == "terminé"
    assert (dest / disparu.path).exists()              # jamais supprimé côté PC
    assert disparu.path in (dest / ".applesync" / "rapports" /
                            f"rapport_{report.run_id}.md").read_text(encoding="utf-8")


def test_interruption_utilisateur_pendant_run(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    seen = {"files": 0}

    def cancel() -> bool:
        return seen["files"] >= 5

    def on_progress(s):
        seen["files"] = s.files_done

    report = engine.execute(prepared, progress=on_progress, cancel=cancel)
    assert report.status == "interrompu"
    assert 0 < len(report.copies) < prepared.inventory.count
    assert report.verification.ok  # le déjà-copié est vérifié conforme

    # Reprise complète ensuite
    report2 = engine.execute(engine.prepare(backend.INFO.udid))
    assert report2.status == "terminé"
    with Manifest(dest) as m:
        assert len(m.all_entries()) == prepared.inventory.count
