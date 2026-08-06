"""Zone iCloud (CPLAssets) : couverte par la sauvegarde comme le DCIM.

Des photos de la bibliothèque peuvent vivre hors DCIM : la synchro couvre
/PhotoData/CPLAssets, chemins d'inventaire préfixés « CPLAssets/ »."""

import io
import time

from applesync.core.engine import SyncEngine
from applesync.core.layout import ArchiveLayout
from applesync.core.manifest import Manifest
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_cpl_couvert_en_miroir(dest):
    backend = SimulatedBackend(SimProfile.small())
    cpl = backend.add_file("CPLAssets/group159/UUID-1234.JPG", 4321, T)

    engine = SyncEngine(backend, dest)
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert (dest / "CPLAssets/group159/UUID-1234.JPG").exists()

    # Idempotence : rien ne repart
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy


def test_cpl_date_par_exif_en_archive(dest):
    """Une photo CPL avec EXIF est classée à sa date de prise, comme les autres."""
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGB", (4, 4), "red")
    exif = Image.Exif()
    exif[306] = "2019:03:08 12-00-00".replace("-", ":")
    im.save(buf, "JPEG", exif=exif)

    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content("CPLAssets/group159/UUID-9.JPG", buf.getvalue(), T)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert (dest / "2019/2019-03/2019-03-08 12-00-00.jpg").exists()


def test_album_avec_element_cpl_apparie_apres_synchro(dest, tmp_path):
    """Un élément d'album stocké en zone iCloud est retrouvé dans la
    sauvegarde une fois la synchro passée (mapping PhotoData/CPLAssets)."""
    import sqlite3

    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("CPLAssets/group159/UUID-7.JPG", 2222, T)
    engine = SyncEngine(backend, dest)
    assert engine.execute(engine.prepare(backend.INFO.udid)).status == "terminé"

    db = tmp_path / "Photos.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZDIRECTORY VARCHAR,
            ZFILENAME VARCHAR, ZFAVORITE INTEGER DEFAULT 0,
            ZTRASHEDSTATE INTEGER DEFAULT 0);
        CREATE TABLE ZGENERICALBUM (Z_PK INTEGER PRIMARY KEY, ZTITLE VARCHAR,
            ZKIND INTEGER, ZTRASHEDSTATE INTEGER DEFAULT 0);
        CREATE TABLE "Z_28ASSETS" ("Z_28ALBUMS" INTEGER, "Z_3ASSETS" INTEGER);
        INSERT INTO ZASSET VALUES (1, 'PhotoData/CPLAssets/group159', 'UUID-7.JPG', 0, 0);
        INSERT INTO ZGENERICALBUM VALUES (10, 'Nuage', 2, 0);
        INSERT INTO "Z_28ASSETS" VALUES (10, 1);
    """)
    con.commit()
    con.close()

    from applesync.core.albums import materialize_albums, parse_albums

    data = parse_albums(db)
    with Manifest(dest) as m:
        report = materialize_albums(data, m, dest)
    assert not report.unmatched
    assert report.copies_created == 1
    assert (dest / "_Albums" / "Nuage" / "UUID-7.JPG").exists()