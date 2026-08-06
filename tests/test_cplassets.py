"""iCloud zone (CPLAssets): covered by the backup, just like DCIM.

Some library photos live outside DCIM, so the sync covers
/PhotoData/CPLAssets, with inventory paths prefixed "CPLAssets/".
"""

import io
import time

from applesync.core.engine import COMPLETED, SyncEngine
from applesync.core.layout import ArchiveLayout
from applesync.core.manifest import Manifest
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_cpl_is_covered_in_the_mirror_layout(dest):
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("CPLAssets/group159/UUID-1234.JPG", 4321, T)

    engine = SyncEngine(backend, dest)
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert (dest / "CPLAssets/group159/UUID-1234.JPG").exists()

    # Idempotence: nothing goes again
    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy


def test_cpl_photos_are_exif_dated_in_the_archive_layout(dest):
    """A CPL photo carrying EXIF is filed at its capture date, like any other."""
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGB", (4, 4), "red")
    exif = Image.Exif()
    exif[306] = "2019:03:08 12:00:00"
    im.save(buf, "JPEG", exif=exif)

    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content("CPLAssets/group159/UUID-9.JPG", buf.getvalue(), T)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert (dest / "2019/2019-03/2019-03-08 12-00-00.jpg").exists()


def test_album_item_in_the_cpl_zone_is_matched_after_a_sync(dest, tmp_path):
    """An album item stored in the iCloud zone is found in the backup once the
    sync has run (PhotoData/CPLAssets mapping)."""
    import sqlite3

    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("CPLAssets/group159/UUID-7.JPG", 2222, T)
    engine = SyncEngine(backend, dest)
    assert engine.execute(engine.prepare(backend.INFO.udid)).status == COMPLETED

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
        INSERT INTO ZGENERICALBUM VALUES (10, 'Cloud', 2, 0);
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
    assert (dest / "_Albums" / "Cloud" / "UUID-7.JPG").exists()
