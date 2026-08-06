"""Incremental plan: identity stronger than the name, idempotence, deletions."""

import os
import time

from applesync.core.inventory import take_inventory
from applesync.core.manifest import Manifest
from applesync.core.planner import build_plan, local_target


def _inventory(backend):
    with backend.connect(backend.INFO.udid) as s:
        return take_inventory(s)


def test_first_run_copies_everything(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest)
    assert len(plan.to_copy) == inv.count
    assert not plan.already_synced
    assert not plan.conflicts
    assert not plan.missing_on_device


def test_nothing_left_to_copy_once_recorded(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        plan = build_plan(inv, m, dest)
    assert not plan.to_copy
    assert len(plan.already_synced) == inv.count


def test_only_the_new_file_is_copied(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        added = backend.add_file("202312_a/IMG_99999.HEIC", 4321, 1_700_000_000)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert [f.path for f in plan.to_copy] == [added.path]


def test_same_name_different_content_is_a_conflict(backend, dest):
    """Identity must go beyond the name: a local file at the target path with
    a different size must NEVER be overwritten."""
    inv = _inventory(backend)
    victim = inv.files[0]
    local = dest / local_target(victim.path)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"diverging local content")  # size != victim.size

    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest)
    conflict = [c for c in plan.conflicts if c.remote.path == victim.path]
    assert len(conflict) == 1
    assert conflict[0].versioned_path != local_target(victim.path)
    assert ".~2" in conflict[0].versioned_path
    # The file is NOT in to_copy (it would overwrite the local one)
    assert victim.path not in [f.path for f in plan.to_copy]


def test_identical_local_file_is_adopted(backend, dest):
    """Manifest lost but the file is already on disk with the exact size and
    mtime: adopt it, do not copy it again."""
    inv = _inventory(backend)
    f = inv.files[0]
    local = dest / local_target(f.path)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"x" * f.size)
    os.utime(local, (time.time(), f.mtime))

    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest)
    assert f.path in [a.path for a in plan.to_adopt]
    assert f.path not in [c.path for c in plan.to_copy]


def test_device_deletion_is_reported_and_the_file_kept(backend, dest):
    inv = _inventory(backend)
    gone = inv.files[5]
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        backend.remove_file(gone.path)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert [e.source_path for e in plan.missing_on_device] == [gone.path]
    # Nothing to copy nor to delete: the local file stays
    assert not plan.to_copy


def test_file_replaced_on_the_device(backend, dest):
    """Same path, new identity: the old version is reported gone, the new one
    is to be copied (as a conflict if a local file exists)."""
    inv = _inventory(backend)
    target = inv.files[7]
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        backend.replace_file(target.path, target.size + 999, target.mtime + 3600)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert target.path in [f.path for f in plan.to_copy]   # no local file -> plain copy
    assert target.identity in [e.identity for e in plan.missing_on_device]
