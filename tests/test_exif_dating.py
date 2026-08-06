"""EXIF dating at placement time (archive layout): the real-world case of
bulk-imported photos whose mtime is not their capture date."""

import io
import time

from applesync.core.engine import COMPLETED, SyncEngine
from applesync.core.exifdate import exif_timestamp
from applesync.core.layout import ArchiveLayout
from applesync.device.simulator import SimProfile, SimulatedBackend

# Typical bulk-import mtime: hundreds of photos sharing one timestamp,
# unrelated to when they were taken.
T_IMPORT = int(time.mktime((2015, 12, 10, 1, 28, 31, 0, 0, -1)))


def _jpeg_with_exif(exif_date: str) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    im = Image.new("RGB", (4, 4), "red")
    exif = Image.Exif()
    exif[306] = exif_date           # DateTime "YYYY:MM:DD HH:MM:SS"
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _jpeg_without_exif() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, "JPEG")
    return buf.getvalue()


def test_exif_timestamp_reads_the_date(tmp_path):
    p = tmp_path / "t.jpg"
    p.write_bytes(_jpeg_with_exif("2009:07:14 18:22:31"))
    ts = exif_timestamp(p)
    assert ts == int(time.mktime((2009, 7, 14, 18, 22, 31, 0, 0, -1)))


def test_exif_timestamp_returns_none_without_exif(tmp_path):
    p = tmp_path / "s.jpg"
    p.write_bytes(_jpeg_without_exif())
    assert exif_timestamp(p) is None
    q = tmp_path / "x.heic"
    q.write_bytes(b"not an image at all")
    assert exif_timestamp(q) is None


def test_bulk_import_is_refiled_by_exif(dest):
    """Three "old" photos sharing one import mtime but carrying distinct EXIF
    dates: filed at their real date, no .~N suffix, no conflict."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_with_exif("2009:07:14 18:22:31"), T_IMPORT)
    backend.add_file_with_content(
        "100APPLE/IMG_0006.JPG", _jpeg_with_exif("2011:01:02 10:00:00"), T_IMPORT)
    backend.add_file_with_content(
        "100APPLE/IMG_0007.JPG", _jpeg_with_exif("2013:05:20 08:15:45"), T_IMPORT)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    prepared = engine.prepare(backend.INFO.udid)
    assert not prepared.plan.conflicts          # no more phantom collisions

    report = engine.execute(prepared)
    assert report.status == COMPLETED
    assert report.verification.ok
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "2011/2011-01/2011-01-02 10-00-00.jpg").exists()
    assert (dest / "2013/2013-05/2013-05-20 08-15-45.jpg").exists()
    assert not (dest / "2015/2015-12/2015-12-10 01-28-31.~2.jpg").exists()
    # No staging leftovers
    staging = dest / ".applesync" / "staging"
    assert not list(staging.glob("*")) if staging.exists() else True


def test_without_exif_the_mtime_is_used(dest):
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content("100APPLE/IMG_0008.JPG", _jpeg_without_exif(),
                                  T_IMPORT)
    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert (dest / "2015/2015-12/2015-12-10 01-28-31.jpg").exists()


def test_live_and_aae_follow_the_exif_date_of_their_photo(dest):
    """The photo is EXIF-dated 2009; its Live MOV and its AAE follow, despite
    2015 mtimes."""
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_with_exif("2009:07:14 18:22:31"), T_IMPORT)
    backend.add_file("100APPLE/IMG_0005.MOV", 9000, T_IMPORT + 2)
    backend.add_file("100APPLE/IMG_0005.AAE", 250, T_IMPORT + 9000)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "_LivePhotos/2009/2009-07/2009-07-14 18-22-31.mov").exists()
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.aae").exists()


def test_true_same_second_exif_collision_is_versioned(dest):
    """Two different contents, same EXIF second: .~2, never a failure."""
    import io as _io

    from PIL import Image

    backend = SimulatedBackend(SimProfile.small())
    backend.add_file_with_content(
        "100APPLE/IMG_0005.JPG", _jpeg_with_exif("2009:07:14 18:22:31"), T_IMPORT)
    # Same EXIF but different pixels -> different content
    buf = _io.BytesIO()
    im = Image.new("RGB", (4, 4), "green")
    exif = Image.Exif()
    exif[306] = "2009:07:14 18:22:31"
    im.save(buf, "JPEG", exif=exif)
    backend.add_file_with_content("100APPLE/IMG_0006.JPG", buf.getvalue(), T_IMPORT)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert not report.failures
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.jpg").exists()
    assert (dest / "2009/2009-07/2009-07-14 18-22-31.~2.jpg").exists()
