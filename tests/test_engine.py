"""End to end on the simulator: prepare -> validate -> execute -> verify."""

import pytest

from applesync.core.engine import COMPLETED, INTERRUPTED, SyncEngine
from applesync.core.inventory import InventoryMismatchError
from applesync.core.journal import read_journal
from applesync.core.manifest import Manifest
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_first_full_run(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    assert prepared.inventory.count == len(backend.tree)
    assert len(prepared.plan.to_copy) == prepared.inventory.count

    report = engine.execute(prepared, deep_verify=True)
    assert report.status == COMPLETED
    assert len(report.copies) == prepared.inventory.count
    assert report.verification is not None and report.verification.ok
    assert report.verification.hashed_count == prepared.inventory.count

    # Report and journal written and readable back
    md = dest / ".applesync" / "reports" / f"report_{report.run_id}.md"
    assert md.exists()
    content = md.read_text(encoding="utf-8")
    assert "COMPLETED" in content and "No discrepancy" in content
    journals = list((dest / ".applesync" / "logs").glob("*.jsonl"))
    assert journals
    events = read_journal(journals[0])
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"


def test_second_run_is_idempotent(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))

    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy
    assert len(prepared2.plan.already_synced) == prepared2.inventory.count
    report2 = engine.execute(prepared2)
    assert report2.status == COMPLETED
    assert not report2.copies
    assert report2.verification.ok


def test_incremental_run_copies_only_the_new_files(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))
    backend.add_file("202312_a/IMG_90001.HEIC", 7000, 1_701_000_000)
    backend.add_file("202312_a/IMG_90002.MOV", 90_000, 1_701_000_060)

    prepared = engine.prepare(backend.INFO.udid)
    assert len(prepared.plan.to_copy) == 2
    report = engine.execute(prepared)
    assert report.status == COMPLETED
    assert len(report.copies) == 2


def test_truncated_inventory_blocks_before_any_copy(dest):
    faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=10)
    backend = SimulatedBackend(SimProfile.small(), faults)
    engine = SyncEngine(backend, dest)
    with pytest.raises(InventoryMismatchError):
        engine.prepare(backend.INFO.udid)
    # Nothing was written into the destination
    assert not any(p for p in dest.rglob("*.HEIC"))
    assert not any(p for p in dest.rglob("*.part"))


def test_disconnection_during_copy_then_resume(dest):
    """Screen locked mid-copy: run interrupted, already-copied files kept and
    verified, resumed on the next run without starting over."""
    prof = SimProfile.small()
    probe = SimulatedBackend(prof)
    ordered = sorted(probe.tree, key=lambda f: f.path)
    victim = ordered[10]
    faults = FaultPlan(
        fail_read_path=victim.path,
        fail_read_at_byte=victim.size // 2,
        fail_read_as_disconnect=True,
    )
    backend = SimulatedBackend(prof, faults)
    engine = SyncEngine(backend, dest)

    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == INTERRUPTED
    assert report.error and "resume byte-exactly" in report.error
    assert len(report.copies) == 10                   # the 10 before the victim
    assert report.verification.ok                     # what is copied is sound
    assert (dest / (victim.path + ".part")).exists()  # partial kept
    assert not (dest / victim.path).exists()          # never a disguised partial

    # Next run without the fault: resume, no re-copy of the first ten
    backend_ok = SimulatedBackend(prof)
    engine2 = SyncEngine(backend_ok, dest)
    prepared2 = engine2.prepare(backend_ok.INFO.udid)
    assert len(prepared2.plan.already_synced) == 10
    report2 = engine2.execute(prepared2)
    assert report2.status == COMPLETED
    resumed = [c for c in report2.copies if c.remote.path == victim.path]
    assert len(resumed) == 1 and resumed[0].resumed_from == victim.size // 2
    assert report2.verification.ok
    assert report2.verification.hashed_count == prepared2.inventory.count


def test_verification_progress_is_reported(backend, dest):
    """Verification reports (done, total, file) all the way — that is what
    drives the UI progress bar."""
    engine = SyncEngine(backend, dest)
    calls = []
    report = engine.execute(
        engine.prepare(backend.INFO.udid),
        verify_progress=lambda i, n, p: calls.append((i, n, p)),
    )
    assert report.status == COMPLETED
    assert calls, "no progress reported"
    last = calls[-1]
    assert last[0] == last[1] == report.verification.checked_count
    assert all(n == last[1] for _, n, _ in calls)
    assert all(p for _, _, p in calls)   # the current file is always named


def test_device_deletion_is_kept_and_reported(backend, dest):
    engine = SyncEngine(backend, dest)
    engine.execute(engine.prepare(backend.INFO.udid))
    gone = backend.tree[0]
    backend.remove_file(gone.path)

    prepared = engine.prepare(backend.INFO.udid)
    assert [e.source_path for e in prepared.plan.missing_on_device] == [gone.path]
    report = engine.execute(prepared)
    assert report.status == COMPLETED
    assert (dest / gone.path).exists()                 # never deleted on the PC
    assert gone.path in (dest / ".applesync" / "reports" /
                         f"report_{report.run_id}.md").read_text(encoding="utf-8")


def test_user_interruption_during_a_run(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    seen = {"files": 0}

    def cancel() -> bool:
        return seen["files"] >= 5

    def on_progress(s):
        seen["files"] = s.files_done

    report = engine.execute(prepared, progress=on_progress, cancel=cancel)
    assert report.status == INTERRUPTED
    assert 0 < len(report.copies) < prepared.inventory.count
    assert report.verification.ok  # what was copied verifies clean

    # Full resume afterwards
    report2 = engine.execute(engine.prepare(backend.INFO.udid))
    assert report2.status == COMPLETED
    with Manifest(dest) as m:
        assert len(m.all_entries()) == prepared.inventory.count
