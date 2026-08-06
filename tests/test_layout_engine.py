"""Layout + engine: archive end to end, collisions, lock, duplicates."""

import time

import pytest

from applesync.core.duplicates import find_duplicates
from applesync.core.engine import COMPLETED, SyncEngine
from applesync.core.inventory import take_inventory
from applesync.core.layout import ArchiveLayout, DateLayout, LayoutLockedError, \
    MirrorLayout
from applesync.core.manifest import Manifest
from applesync.core.planner import build_plan
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_archive_end_to_end(dest):
    """Full sync in the archive layout: Live Photo paired, PNG in the monthly
    flow, deep verification clean, idempotence."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("100APPLE/IMG_9001.HEIC", 5000, T)
    backend.add_file("100APPLE/IMG_9001.MOV", 20000, T + 3)
    backend.add_file("100APPLE/IMG_9002.PNG", 800, T + 60)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok

    assert (dest / "2024/2024-08/2024-08-15 14-30-22.heic").exists()
    assert (dest / "_LivePhotos/2024/2024-08/2024-08-15 14-30-22.mov").exists()
    assert (dest / "2024/2024-08/2024-08-15 14-31-22.png").exists()
    # Nothing at the mirror path
    assert not (dest / "100APPLE").exists()

    # Idempotence: nothing left to copy on the next round
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy and not prepared2.plan.conflicts


def test_same_second_collision_is_versioned(dest):
    """Burst: two photos on the same second -> .~2 targets, never a failure."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("100APPLE/IMG_9101.HEIC", 4000, T)
    backend.add_file("101APPLE/IMG_9990.HEIC", 6000, T)   # same second

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert not report.failures
    assert report.verification.ok
    base = dest / "2024/2024-08"
    assert (base / "2024-08-15 14-30-22.heic").exists()
    assert (base / "2024-08-15 14-30-22.~2.heic").exists()


def test_changing_the_layout_is_refused(backend, dest):
    engine = SyncEngine(backend, dest, MirrorLayout())
    engine.execute(engine.prepare(backend.INFO.udid))

    engine2 = SyncEngine(backend, dest, ArchiveLayout())
    with pytest.raises(LayoutLockedError) as exc:
        engine2.prepare(backend.INFO.udid)
    assert "Mirror" in str(exc.value)

    # The right layout still goes through
    engine3 = SyncEngine(backend, dest, MirrorLayout())
    prepared = engine3.prepare(backend.INFO.udid)
    assert not prepared.plan.to_copy


def test_a_legacy_manifest_counts_as_mirror(backend, dest):
    """A populated manifest without meta (older versions) means mirror."""
    engine = SyncEngine(backend, dest, MirrorLayout())
    engine.execute(engine.prepare(backend.INFO.udid))
    with Manifest(dest) as m:
        m._con.execute("DELETE FROM meta")
        m._con.commit()
        assert m.locked_layout() == "mirror"


def test_adoption_in_the_date_layout(backend, dest):
    """Destination pre-filled at the right dated path -> adopted, not copied."""
    import os

    with backend.connect(backend.INFO.udid) as s:
        inv = take_inventory(s)
    f = inv.files[0]
    lay = DateLayout(False)
    target = dest / lay.target_for(f)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * f.size)
    os.utime(target, (time.time(), f.mtime))

    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest, lay)
    assert f.path in [a.path for a in plan.to_adopt]


def test_duplicates_are_filed_during_an_archive_sync(dest):
    """Archive layout: the second copy of the same content goes to
    _Duplicates/ automatically, structure preserved, verification clean."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    clone = backend.clone_file(src.path, "999APPLE/IMG_9999.HEIC", src.mtime + 777)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert len(report.duplicates_routed) == 1
    src_path, dup_rel, original = report.duplicates_routed[0]
    assert src_path == clone.path                      # the clone (second one)
    assert dup_rel.startswith("_Duplicates/")
    assert (dest / dup_rel).exists()
    assert (dest / original).exists()                  # first copy in the flow
    assert not original.startswith("_Duplicates")
    # The Markdown report names it
    md = report.to_markdown()
    assert "_Duplicates/" in md and clone.path in md
    # Idempotence: nothing moves on the next round
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy and not prepared2.plan.conflicts


def test_incremental_duplicate_is_filed_against_the_manifest(dest):
    """A duplicate appearing AFTER the initial sync is filed on the next run
    (detected against the manifest, not just within the run)."""
    backend = SimulatedBackend(SimProfile.small())
    engine = SyncEngine(backend, dest, ArchiveLayout())
    engine.execute(engine.prepare(backend.INFO.udid))

    src = backend.tree[0]
    backend.clone_file(src.path, "999APPLE/IMG_8888.HEIC", src.mtime + 42)
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert len(report.duplicates_routed) == 1
    assert report.duplicates_routed[0][1].startswith("_Duplicates/")
    assert report.verification.ok


def test_duplicates_are_not_filed_in_the_mirror_layout(dest):
    """Outside the archive layout, no filing: a faithful 1:1 copy."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    clone = backend.clone_file(src.path, "999APPLE/IMG_7777.HEIC", src.mtime + 5)
    engine = SyncEngine(backend, dest, MirrorLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert not report.duplicates_routed
    assert (dest / src.path).exists()
    assert (dest / clone.path).exists()


def test_duplicates_detected_by_content(backend, dest):
    """Two manifest entries with identical content -> one named group."""
    engine = SyncEngine(backend, dest, MirrorLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED

    with Manifest(dest) as m:
        before = find_duplicates(m)
        # The simulator produces all-distinct contents
        assert not before.groups and before.scanned_count == len(backend.tree)

        # Fabricate a duplicate: same sha256/size under two paths
        e = m.all_entries()[0]
        from applesync.device.base import RemoteFile

        clone = RemoteFile(path="999APPLE/CLONE.HEIC", size=e.size,
                           mtime=e.mtime + 1)
        m.record_file(clone, e.sha256, "999APPLE/CLONE.HEIC", "run-x", "UDID")
        report2 = find_duplicates(m)

    assert len(report2.groups) == 1
    g = report2.groups[0]
    assert len(g.entries) == 2
    assert g.wasted_bytes == e.size
    md = report2.to_markdown()
    assert e.local_path in md and "CLONE.HEIC" in md
    assert "deletes nothing" in md
