"""iCloud shared albums (PhotoCloudSharingData): covered, but filed apart.

They hold other people's photos: in dated layouts they go to _SharedAlbums/
(per-album structure preserved), never mixed into the personal archive, and
never "filed as duplicates".
"""

import time

from applesync.core.engine import COMPLETED, SyncEngine
from applesync.core.layout import ArchiveLayout, DateLayout, shared_target
from applesync.device.base import RemoteFile
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_target_is_apart_in_dated_layouts():
    f = RemoteFile(path="PhotoCloudSharingData/GUID-42/100SHARE/IMG_1.JPG",
                   size=100, mtime=T)
    assert shared_target(f.path) == "_SharedAlbums/GUID-42/100SHARE/IMG_1.JPG"
    assert DateLayout(False).target_for(f) == \
        "_SharedAlbums/GUID-42/100SHARE/IMG_1.JPG"
    lay = ArchiveLayout()
    lay.begin([f])
    assert lay.target_for(f) == "_SharedAlbums/GUID-42/100SHARE/IMG_1.JPG"
    # Ordinary files are unaffected
    assert shared_target("100APPLE/IMG_1.JPG") is None


def test_shared_items_are_covered_without_dating(dest):
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("PhotoCloudSharingData/GUID-1/IMG_S1.JPG", 3000, T)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert (dest / "_SharedAlbums/GUID-1/IMG_S1.JPG").exists()

    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy          # idempotent


def test_a_shared_copy_of_a_library_photo_is_not_filed_as_a_duplicate(dest):
    """The same photo present in the camera roll AND in a shared album: both
    copies stay where they belong, neither goes to _Duplicates."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    backend.clone_file(src.path, "PhotoCloudSharingData/GUID-1/CLONE.HEIC",
                       src.mtime + 10)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == COMPLETED
    assert report.verification.ok
    assert not report.duplicates_routed
    assert (dest / "_SharedAlbums/GUID-1/CLONE.HEIC").exists()
    assert not (dest / "_Duplicates").exists()
