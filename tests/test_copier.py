"""Copy: exact bytes, byte-exact resume, never a partial file in disguise."""

import pytest

from applesync.core.copier import CopyCancelled, CopyError, copy_file
from applesync.core.journal import Journal
from applesync.device.base import DeviceDisconnectedError, FileReadError
from applesync.device.simulator import (
    FaultPlan,
    SimProfile,
    SimulatedBackend,
    content_sha256,
)


def _journal(dest):
    return Journal(dest, "test-run")


def test_plain_copy_writes_the_exact_bytes(backend, dest):
    f = backend.tree[0]
    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    target = dest / f.path
    assert target.exists()
    assert target.stat().st_size == f.size
    assert res.sha256 == content_sha256(backend.profile.seed, f.path, f.size)
    assert int(target.stat().st_mtime) == f.mtime
    assert res.resumed_from == 0
    # No leftovers
    assert not (dest / (f.path + ".part")).exists()
    assert not (dest / (f.path + ".part.meta.json")).exists()


def test_read_failing_mid_file_leaves_a_part(dest):
    prof = SimProfile.small()
    tree_probe = SimulatedBackend(prof).tree
    f = tree_probe[2]
    faults = FaultPlan(fail_read_path=f.path, fail_read_at_byte=f.size // 2)
    backend = SimulatedBackend(prof, faults)

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(FileReadError):
            copy_file(s, f, dest, f.path, _journal(dest))

    # The final file does NOT exist; the .part and its sidecar remain
    assert not (dest / f.path).exists()
    part = dest / (f.path + ".part")
    assert part.exists()
    assert part.stat().st_size == f.size // 2


def test_disconnection_mid_file_then_resume_with_correct_hash(dest):
    """THE screen-lock scenario: cut mid-file, resume at the exact byte, and
    the final SHA-256 is that of the complete file."""
    prof = SimProfile.small()
    tree_probe = SimulatedBackend(prof).tree
    f = tree_probe[4]
    cut = f.size // 3
    faults = FaultPlan(
        fail_read_path=f.path, fail_read_at_byte=cut, fail_read_as_disconnect=True
    )
    backend = SimulatedBackend(prof, faults)

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            copy_file(s, f, dest, f.path, _journal(dest))
    part = dest / (f.path + ".part")
    assert part.exists() and part.stat().st_size == cut
    assert not (dest / f.path).exists()

    # Reconnect without the fault: the copy resumes exactly at `cut`
    backend2 = SimulatedBackend(prof)  # same seed -> same bytes
    with backend2.connect(backend2.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    assert res.resumed_from == cut
    assert res.bytes_copied_this_run == f.size - cut
    assert res.sha256 == content_sha256(prof.seed, f.path, f.size)
    assert (dest / f.path).stat().st_size == f.size


def test_resume_refused_when_the_source_identity_changed(dest):
    """A .part from an older version of the file must never be completed with
    the bytes of the new one: different identity -> start over."""
    prof = SimProfile.small()
    backend = SimulatedBackend(prof)
    f = backend.tree[6]
    cut = f.size // 2
    faults = FaultPlan(
        fail_read_path=f.path, fail_read_at_byte=cut, fail_read_as_disconnect=True
    )
    backend_faulty = SimulatedBackend(prof, faults)
    with backend_faulty.connect(backend_faulty.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            copy_file(s, f, dest, f.path, _journal(dest))

    # The file changes on the device (replaced: other size, other mtime)
    replacement = backend.replace_file(f.path, f.size + 100, f.mtime + 60)
    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, replacement, dest, replacement.path, _journal(dest))
    assert res.resumed_from == 0          # NO resume on the stale partial
    assert res.sha256 == content_sha256(prof.seed, f.path, replacement.size)


def test_clean_cancellation_then_resume(dest):
    prof = SimProfile.small()
    backend = SimulatedBackend(prof)
    f = max(backend.tree, key=lambda x: x.size)  # the largest, several blocks
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2   # let ~2 checks pass, then interrupt

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(CopyCancelled):
            copy_file(s, f, dest, f.path, _journal(dest), cancel=cancel,
                      chunk_size=16_384)
    part = dest / (f.path + ".part")
    assert part.exists()
    done = part.stat().st_size
    assert 0 < done < f.size

    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    assert res.resumed_from == done
    assert res.sha256 == content_sha256(prof.seed, f.path, f.size)


def test_occupied_target_is_refused(backend, dest):
    f = backend.tree[0]
    target = dest / f.path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"occupant")
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(CopyError):
            copy_file(s, f, dest, f.path, _journal(dest))
    assert target.read_bytes() == b"occupant"  # never overwritten
