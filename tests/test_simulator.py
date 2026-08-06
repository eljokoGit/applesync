"""Le simulateur lui-même doit être digne de confiance : déterminisme prouvé."""

import hashlib

from applesync.device.simulator import (
    FaultPlan,
    SimProfile,
    SimulatedBackend,
    build_tree,
    content_sha256,
    content_stream,
)


def test_meme_seed_meme_arbre():
    a = build_tree(SimProfile.small(seed=7))
    b = build_tree(SimProfile.small(seed=7))
    assert a == b
    c = build_tree(SimProfile.small(seed=8))
    assert a != c


def test_profil_petit_a_une_taille_raisonnable():
    tree = build_tree(SimProfile.small())
    assert 100 <= len(tree) <= 300
    assert all(f.path.count("/") == 1 for f in tree)
    # Sous-dossiers au format YYYYMM_a
    folders = {f.path.split("/")[0] for f in tree}
    assert all(len(name) == 8 and name.endswith("_a") for name in folders)


def test_profil_realiste_vise_40k_fichiers_109go():
    tree = build_tree(SimProfile.realistic())
    total = sum(f.size for f in tree)
    assert 30_000 <= len(tree) <= 55_000
    # ~109 Go réels : on tolère une fourchette large mais du bon ordre
    assert 90 * 10**9 <= total <= 140 * 10**9


def test_contenu_deterministe_et_adressable():
    prof = SimProfile.small()
    tree = build_tree(prof)
    f = tree[0]
    whole = b"".join(content_stream(prof.seed, f.path, f.size))
    assert len(whole) == f.size
    # Lecture reprise à un offset arbitraire : mêmes octets
    off = f.size // 3
    tail = b"".join(content_stream(prof.seed, f.path, f.size, offset=off))
    assert tail == whole[off:]
    # Le hachage de référence correspond
    assert hashlib.sha256(whole).hexdigest() == content_sha256(prof.seed, f.path, f.size)


def test_lecture_via_session_identique_au_flux():
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
