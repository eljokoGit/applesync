"""Plan incrémental : critère plus fort que le nom, idempotence, disparitions."""

import os
import time

from applesync.core.inventory import take_inventory
from applesync.core.manifest import Manifest
from applesync.core.planner import build_plan, local_target
from applesync.device.simulator import SimProfile, SimulatedBackend


def _inventory(backend):
    with backend.connect(backend.INFO.udid) as s:
        return take_inventory(s)


def test_premier_passage_tout_a_copier(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest)
    assert len(plan.to_copy) == inv.count
    assert not plan.already_synced
    assert not plan.conflicts
    assert not plan.missing_on_device


def test_apres_enregistrement_plus_rien_a_copier(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        plan = build_plan(inv, m, dest)
    assert not plan.to_copy
    assert len(plan.already_synced) == inv.count


def test_nouveau_fichier_seul_a_copier(backend, dest):
    inv = _inventory(backend)
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        nouveau = backend.add_file("202312_a/IMG_99999.HEIC", 4321, 1_700_000_000)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert [f.path for f in plan.to_copy] == [nouveau.path]


def test_meme_nom_contenu_different_est_un_conflit(backend, dest):
    """Le critère d'identité doit dépasser le nom : un fichier local présent au
    chemin cible mais de taille différente ne doit JAMAIS être écrasé."""
    inv = _inventory(backend)
    victime = inv.files[0]
    local = dest / local_target(victime.path)
    local.parent.mkdir(parents=True)
    local.write_bytes(b"contenu local divergent")  # taille != victime.size

    with Manifest(dest) as m:
        plan = build_plan(inv, m, dest)
    conflit = [c for c in plan.conflicts if c.remote.path == victime.path]
    assert len(conflit) == 1
    assert conflit[0].versioned_path != local_target(victime.path)
    assert ".~2" in conflit[0].versioned_path
    # Le fichier n'est PAS dans to_copy (il irait écraser le local)
    assert victime.path not in [f.path for f in plan.to_copy]


def test_adoption_fichier_local_identique(backend, dest):
    """Manifeste perdu mais fichier déjà sur disque avec taille+mtime exacts :
    adopté, pas re-copié."""
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


def test_suppression_sur_iphone_signalee_fichier_conserve(backend, dest):
    inv = _inventory(backend)
    disparu = inv.files[5]
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        backend.remove_file(disparu.path)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert [e.source_path for e in plan.missing_on_device] == [disparu.path]
    # Rien à copier ni à supprimer : le local reste
    assert not plan.to_copy


def test_fichier_remplace_sur_iphone(backend, dest):
    """Même chemin, nouvelle identité : l'ancienne version est signalée disparue,
    la nouvelle est à copier (en conflit si le local existe)."""
    inv = _inventory(backend)
    cible = inv.files[7]
    with Manifest(dest) as m:
        for f in inv.files:
            m.record_file(f, "deadbeef", local_target(f.path), "run1", inv.device_udid)
        backend.replace_file(cible.path, cible.size + 999, cible.mtime + 3600)
        inv2 = _inventory(backend)
        plan = build_plan(inv2, m, dest)
    assert cible.path in [f.path for f in plan.to_copy]          # local absent → copie simple
    assert cible.identity in [e.identity for e in plan.missing_on_device]
