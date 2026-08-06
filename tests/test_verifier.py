"""Vérification : chaque altération de la destination est nommée."""

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


def test_tout_conforme(backend, dest):
    inv, m = _sync_everything(backend, dest)
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert rep.ok
    assert rep.ok_count == inv.count
    assert rep.hashed_count == inv.count
    m.close()


def test_fichier_supprime_du_disque_nomme(backend, dest):
    inv, m = _sync_everything(backend, dest)
    victime = inv.files[3]
    (dest / victime.path).unlink()
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert not rep.ok
    assert [d.source_path for d in rep.discrepancies] == [victime.path]
    assert rep.discrepancies[0].kind == "fichier_manquant"
    m.close()


def test_fichier_tronque_nomme(backend, dest):
    inv, m = _sync_everything(backend, dest)
    victime = inv.files[8]
    p = dest / victime.path
    p.write_bytes(p.read_bytes()[:-10])
    rep = verify_against_inventory(inv.files, m, dest, deep_hash=False)
    assert [d.source_path for d in rep.discrepancies] == [victime.path]
    assert rep.discrepancies[0].kind == "taille"
    m.close()


def test_corruption_meme_taille_detectee_par_hachage(backend, dest):
    """Corruption d'un octet sans changement de taille : seul le mode
    approfondi la voit — c'est sa raison d'être."""
    inv, m = _sync_everything(backend, dest)
    victime = inv.files[10]
    p = dest / victime.path
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF
    p.write_bytes(bytes(data))

    rapide = verify_against_inventory(inv.files, m, dest, deep_hash=False)
    assert rapide.ok  # la taille ne bouge pas : le contrôle rapide ne voit rien

    profond = verify_against_inventory(inv.files, m, dest, deep_hash=True)
    assert [d.source_path for d in profond.discrepancies] == [victime.path]
    assert profond.discrepancies[0].kind == "sha256"
    m.close()


def test_fichier_jamais_copie_absent_du_manifeste(backend, dest):
    inv, m = _sync_everything(backend, dest)
    nouveau = backend.add_file("202312_a/IMG_88888.HEIC", 5000, 1_700_000_000)
    with backend.connect(backend.INFO.udid) as s:
        inv2 = take_inventory(s)
    rep = verify_against_inventory(inv2.files, m, dest, deep_hash=False)
    assert [d.source_path for d in rep.discrepancies] == [nouveau.path]
    assert rep.discrepancies[0].kind == "absent_du_manifeste"
    m.close()
