"""Verification: every alteration of the destination is named."""

from applesync.core.copier import copy_file
from applesync.core.inventory import take_inventory
from applesync.core.journal import Journal
from applesync.core.manifest import Manifest
from applesync.core.verifier import verify_against_inventory


def _sync_everything(backend, dest):
    with backend.connect(backend.INFO.udid) as s:
        inv = take_inventory(s)
    m = Manifest(dest)
    j = Journal(dest, "test-run")
    with backend.connect(backend.INFO.udid) as s:
        for f in inv.files:
            res = copy_file(s, f, dest, f.path, j)
            m.record_file(f, res.sha256, res.local_relpath, "test-run", inv.device_udid)
    return inv, m


def test_everything_conforms(backend, dest):
    inv, m = _sync_everything(backend, dest)
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert rep.ok
    assert rep.ok_count == inv.count
    assert rep.hashed_count == inv.count
    m.close()


def test_file_deleted_from_disk_is_named(backend, dest):
    inv, m = _sync_everything(backend, dest)
    victim = inv.files[3]
    (dest / victim.path).unlink()
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert not rep.ok
    assert [d.source_path for d in rep.discrepancies] == [victim.path]
    assert rep.discrepancies[0].kind == "file_missing"
    m.close()


def test_truncated_file_is_named(backend, dest):
    inv, m = _sync_everything(backend, dest)
    victim = inv.files[8]
    p = dest / victim.path
    p.write_bytes(p.read_bytes()[:-10])
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=False)
    assert [d.source_path for d in rep.discrepancies] == [victim.path]
    assert rep.discrepancies[0].kind == "size"
    m.close()


def test_same_size_corruption_is_caught_by_hashing(backend, dest):
    """One byte flipped without changing the size: only the deep mode sees it
    — that is its whole purpose."""
    inv, m = _sync_everything(backend, dest)
    victim = inv.files[10]
    p = dest / victim.path
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF
    p.write_bytes(bytes(data))

    quick = verify_against_inventory(inv.files, m, dest, deep_hash=False)
    assert quick.ok  # the size did not move: the quick check sees nothing

    deep = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert [d.source_path for d in deep.discrepancies] == [victim.path]
    assert deep.discrepancies[0].kind == "sha256"
    m.close()


def test_never_copied_file_is_absent_from_the_manifest(backend, dest):
    inv, m = _sync_everything(backend, dest)
    added = backend.add_file("202312_a/IMG_88888.HEIC", 5000, 1_700_000_000)
    with backend.connect(backend.INFO.udid) as s:
        inv2 = take_inventory(s)
    rep = verify_against_inventory(inv2.files, m, dest, deep_hash=False)
    assert [d.source_path for d in rep.discrepancies] == [added.path]
    assert rep.discrepancies[0].kind == "not_in_manifest"
    m.close()
