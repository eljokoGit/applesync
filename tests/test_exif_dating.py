"""Datation EXIF au placement (organisation archive) : le scénario réel des
photos importées en masse dont le mtime n'est pas la date de prise de vue."""

import io
import time

from applesync.core.engine import SyncEngine
from applesync.core.exifdate import exif_timestamp
from applesync.core.layout import ArchiveLayout
from applesync.device.simulator import SimProfile, SimulatedBackend

# mtime typique d'un import en masse : des centaines de photos au même
# horodatage, sans rapport avec leur date de prise de vue.
T_IMPORT = int(time.mktime((2015, 12, 10, 1, 28, 31, 0, 0, -1)))


def _jpeg_avec_exif(date_exif: str) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGB", (4, 4), "red")
    exif = Image.Exif()
    exif[306] = date_exif           # DateTime "AAAA:MM:JJ HH:MM:SS"
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _jpeg_sans_exif() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, "JPEG")
    return buf.getvalue()


def test_exif_timestamp_lit_la_date(tmp_path):
    p = tmp_path / "t.jpg"
    p.write_bytes(_jpeg_avec_exif("2009:07:14 18:22:31"))
    ts = exif_timestamp(p)
    assert ts == int(time.mktime((2009, 7, 14, 18, 22, 31, 0, 0, -1)))


def test_exif_timestamp_sans_exif_rend_none(tmp_path):
    p = tmp_path / "s.jpg"
    p.write_bytes(_jpeg_sans_exif())
    assert exif_timestamp(p) is None
    q = tmp_path / "x.heic"
    q.write_bytes(b"pas une image du tout")
    assert exif_timestamp(q) is None


def test_import_en_masse_reclasse_par_exif(dest):
    """Trois « vieilles » photos au même mtime d'import mais aux EXIF
    distincts : classées à leur vraie date, zéro suffixe .~N, zéro conflit."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_avec_exif("2009:07:14 18:22:31"), T_IMPORT)
    backend.add_file_with_content(
        "100APPLE/IMG_0006.JPG", _jpeg_avec_exif("2011:01-02 10:00:00".replace("-", ":")), T_IMPORT)
    backend.add_file_with_content(
        "100APPLE/IMG_0007.JPG", _jpeg_avec_exif("2013:05:20 08:15:45"), T_IMPORT)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    prepared = engine.prepare(backend.INFO.udid)
    assert not prepared.plan.conflicts          # plus de fausses collisions au plan

    report = engine.execute(prepared)
    assert report.status == "terminé"
    assert report.verification.ok
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "2011/2011-01/2011-01-02 10-00-00.jpg").exists()
    assert (dest / "2013/2013-05/2013-05-20 08-15-45.jpg").exists()
    assert not (dest / "2015/2015-12/2015-12-10 01-28-31.~2.jpg").exists()
    # Aucun résidu de transit
    assert not list((dest / ".applesync" / "transit").glob("*")) \
        if (dest / ".applesync" / "transit").exists() else True


def test_sans_exif_repli_sur_mtime(dest):
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content("100APPLE/IMG_0008.JPG", _jpeg_sans_exif(), T_IMPORT)
    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert (dest / "2015/2015-12/2015-12-10 01-28-31.jpg").exists()


def test_live_et_aae_suivent_la_date_exif_de_leur_photo(dest):
    """La photo est datée EXIF 2009 ; son MOV Live et son AAE la suivent,
    malgré des mtimes 2015."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_avec_exif("2009:07:14 18:22:31"), T_IMPORT)
    backend.add_file("100APPLE/IMG_0005.MOV", 9000, T_IMPORT + 2)
    backend.add_file("100APPLE/IMG_0005.AAE", 250, T_IMPORT + 9000)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "_LivePhotos/2009/2009-07/2009-07-14 18-22-31.mov").exists()
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.aae").exists()


def test_vraie_collision_meme_seconde_exif_versionnee(dest):
    """Deux contenus différents, même date EXIF à la seconde : .~2, pas d'échec."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_avec_exif("2009:07:14 18:22:31"), T_IMPORT)
    # EXIF identique mais pixels différents → contenu différent
    from PIL import Image
    import io as _io

    buf = _io.BytesIO()
    im = Image.new("RGB", (4, 4), "green")
    exif = Image.Exif()
    exif[306] = "2009:07:14 18:22:31"
    im.save(buf, "JPEG", exif=exif)
    backend.add_file_with_content("100APPLE/IMG_0006.JPG", buf.getvalue(), T_IMPORT)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert not report.failures
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.~2.jpg").exists()
