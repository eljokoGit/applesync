"""Organisation + moteur : bout-en-bout archive, collisions, verrou, doublons."""

import time

import pytest

from applesync.core.duplicates import find_duplicates
from applesync.core.engine import SyncEngine
from applesync.core.inventory import take_inventory
from applesync.core.layout import ArchiveLayout, DateLayout, LayoutLockedError, MirrorLayout
from applesync.core.manifest import Manifest
from applesync.core.planner import build_plan
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_bout_en_bout_archive(dest):
    """Synchro complète en disposition archive : Live Photo appariée, PNG dans
    le flux mensuel, vérification profonde OK, idempotence."""
    backend = SimulatedBackend(SimProfile.small())
    photo = backend.add_file("100APPLE/IMG_9001.HEIC", 5000, T)
    live = backend.add_file("100APPLE/IMG_9001.MOV", 20000, T + 3)
    backend.add_file("100APPLE/IMG_9002.PNG", 800, T + 60)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok

    assert (dest / "2024/2024-08/2024-08-15 14-30-22.heic").exists()
    assert (dest / "_LivePhotos/2024/2024-08/2024-08-15 14-30-22.mov").exists()
    assert (dest / "2024/2024-08/2024-08-15 14-31-22.png").exists()
    # Rien au chemin miroir
    assert not (dest / "100APPLE").exists()

    # Idempotence : plus rien à copier au tour suivant
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy and not prepared2.plan.conflicts


def test_collision_meme_seconde_versionnee(dest):
    """Rafale : deux photos à la même seconde → cibles .~2, jamais d'échec."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("100APPLE/IMG_9101.HEIC", 4000, T)
    backend.add_file("101APPLE/IMG_9990.HEIC", 6000, T)   # même seconde

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert not report.failures
    assert report.verification.ok
    base = dest / "2024/2024-08"
    assert (base / "2024-08-15 14-30-22.heic").exists()
    assert (base / "2024-08-15 14-30-22.~2.heic").exists()


def test_verrou_changement_dorganisation_refuse(backend, dest):
    engine = SyncEngine(backend, dest, MirrorLayout())
    engine.execute(engine.prepare(backend.INFO.udid))

    engine2 = SyncEngine(backend, dest, ArchiveLayout())
    with pytest.raises(LayoutLockedError) as exc:
        engine2.prepare(backend.INFO.udid)
    assert "Miroir" in str(exc.value)

    # La bonne organisation, elle, passe toujours
    engine3 = SyncEngine(backend, dest, MirrorLayout())
    prepared = engine3.prepare(backend.INFO.udid)
    assert not prepared.plan.to_copy


def test_manifeste_ancien_vaut_miroir(backend, dest):
    """Un manifeste peuplé sans meta (versions antérieures) = miroir figé."""
    engine = SyncEngine(backend, dest, MirrorLayout())
    engine.execute(engine.prepare(backend.INFO.udid))
    with Manifest(dest) as m:
        m._con.execute("DELETE FROM meta")
        m._con.commit()
        assert m.locked_layout() == "miroir"


def test_adoption_en_disposition_date(backend, dest):
    """Destination pré-remplie au bon chemin date → adoptée, pas re-copiée."""
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


def test_doublons_ranges_pendant_la_synchro_archive(dest):
    """Organisation archive : le second exemplaire d'un même contenu part
    automatiquement sous _Doublons/, structure conservée, vérification OK."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    clone = backend.clone_file(src.path, "999APPLE/IMG_9999.HEIC", src.mtime + 777)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert len(report.duplicates_routed) == 1
    src_path, dup_rel, original = report.duplicates_routed[0]
    assert src_path == clone.path                      # le clone (2e en ordre)
    assert dup_rel.startswith("_Doublons/")
    assert (dest / dup_rel).exists()
    assert (dest / original).exists()                  # premier exemplaire en flux
    assert not original.startswith("_Doublons")
    # Le rapport Markdown le nomme
    md = report.to_markdown()
    assert "_Doublons/" in md and clone.path in md
    # Idempotence : rien ne repart, rien ne bouge
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy and not prepared2.plan.conflicts


def test_doublon_incremental_range_contre_manifeste(dest):
    """Un doublon apparu APRÈS la synchro initiale est rangé au run suivant
    (détection contre le manifeste, pas seulement dans le run)."""
    backend = SimulatedBackend(SimProfile.small())
    engine = SyncEngine(backend, dest, ArchiveLayout())
    engine.execute(engine.prepare(backend.INFO.udid))

    src = backend.tree[0]
    backend.clone_file(src.path, "999APPLE/IMG_8888.HEIC", src.mtime + 42)
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert len(report.duplicates_routed) == 1
    assert report.duplicates_routed[0][1].startswith("_Doublons/")
    assert report.verification.ok


def test_doublons_non_ranges_en_miroir(dest):
    """Hors organisation archive, pas de rangement : copie fidèle 1:1."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    clone = backend.clone_file(src.path, "999APPLE/IMG_7777.HEIC", src.mtime + 5)
    engine = SyncEngine(backend, dest, MirrorLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert not report.duplicates_routed
    assert (dest / src.path).exists()
    assert (dest / clone.path).exists()


def test_doublons_detectes_par_contenu(backend, dest):
    """Deux fichiers de contenu identique au manifeste → un groupe nominatif."""
    engine = SyncEngine(backend, dest, MirrorLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"

    with Manifest(dest) as m:
        avant = find_duplicates(m)
        # Le simulateur produit des contenus tous distincts
        assert not avant.groups and avant.scanned_count == len(backend.tree)

        # On fabrique un doublon : même sha256/taille sous deux chemins
        e = m.all_entries()[0]
        from applesync.device.base import RemoteFile

        clone = RemoteFile(path="999APPLE/CLONE.HEIC", size=e.size,
                           mtime=e.mtime + 1)
        m.record_file(clone, e.sha256, "999APPLE/CLONE.HEIC", "run-x", "UDID")
        rapport = find_duplicates(m)

    assert len(rapport.groups) == 1
    g = rapport.groups[0]
    assert len(g.entries) == 2
    assert g.wasted_bytes == e.size
    md = rapport.to_markdown()
    assert e.local_path in md and "CLONE.HEIC" in md
    assert "ne supprime rien" in md
