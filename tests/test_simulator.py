"""The simulator itself must be trustworthy: determinism, proven."""

import hashlib

from applesync.device.simulator import (
    SimProfile,
    SimulatedBackend,
    build_tree,
    content_sha256,
    content_stream,
)


def test_same_seed_same_tree():
    a = build_tree(SimProfile.small(seed=7))
    b = build_tree(SimProfile.small(seed=7))
    assert a == b
    c = build_tree(SimProfile.small(seed=8))
    assert a != c


def test_small_profile_has_a_sane_size():
    tree = build_tree(SimProfile.small())
    assert 100 <= len(tree) <= 300
    assert all(f.path.count("/") == 1 for f in tree)
    folders = {f.path.split("/")[0] for f in tree}
    assert all(len(name) == 8 and name.endswith("_a") for name in folders)


def test_realistic_profile_targets_a_large_library():
    tree = build_tree(SimProfile.realistic())
    total = sum(f.size for f in tree)
    assert 30_000 <= len(tree) <= 55_000
    assert 90 * 10**9 <= total <= 140 * 10**9


def test_content_is_deterministic_and_addressable():
    prof = SimProfile.small()
    tree = build_tree(prof)
    f = tree[0]
    whole = b"".join(content_stream(prof.seed, f.path, f.size))
    assert len(whole) == f.size
    # Reading resumed at an arbitrary offset yields the same bytes
    off = f.size // 3
    tail = b"".join(content_stream(prof.seed, f.path, f.size, offset=off))
    assert tail == whole[off:]
    # The reference hash matches
    assert hashlib.sha256(whole).hexdigest() == content_sha256(prof.seed, f.path, f.size)


def test_reading_through_a_session_matches_the_stream():
    prof = SimProfile.small()
    backend = SimulatedBackend(prof)
    f = backend.tree[3]
    with backend.connect(backend.INFO.udid) as session:
        with session.open_file(f.path) as reader:
            data = b""
            while True:
                chunk = reader.read(1000)
                if not chunk:
                    break
                data += chunk
    assert data == b"".join(content_stream(prof.seed, f.path, f.size))
