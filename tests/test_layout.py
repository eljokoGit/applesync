"""Layout strategies: exact targets, Live Photo / AAE pairing."""

import time

import pytest

from applesync.core.layout import (
    ArchiveLayout,
    DateLayout,
    LayoutLockedError,
    MirrorLayout,
    label_for,
    layout_from_id,
    make_layout,
)
from applesync.device.base import RemoteFile


def _f(path: str, mtime: int, size: int = 1000) -> RemoteFile:
    return RemoteFile(path=path, size=size, mtime=mtime)


# Reference mtime: 2024-08-15 14:30:22 local time
T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_mirror_is_passthrough():
    assert MirrorLayout().target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "100APPLE/IMG_0001.HEIC"


def test_date_layout_keeps_original_names():
    lay = DateLayout(screenshots_apart=False)
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/IMG_0001.HEIC"
    # PNG not separated when the option is off
    assert lay.target_for(_f("100APPLE/IMG_0002.PNG", T)) == \
        "2024/2024-08/IMG_0002.PNG"


def test_date_layout_with_screenshots_apart():
    lay = DateLayout(screenshots_apart=True)
    assert lay.target_for(_f("100APPLE/IMG_0002.PNG", T)) == \
        "2024/2024-08/Screenshots/IMG_0002.PNG"
    # MP4s are ordinary videos (messaging, imports): monthly flow, never in
    # Screenshots.
    assert lay.target_for(_f("100APPLE/IMG_0003.MP4", T)) == \
        "2024/2024-08/IMG_0003.MP4"
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/IMG_0001.HEIC"


def test_archive_renames_with_a_timestamp():
    lay = ArchiveLayout()
    lay.begin([])
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/2024-08-15 14-30-22.heic"


def test_archive_pairs_live_photos():
    """The MOV of a Live Photo (photo of the same name, same folder) goes to
    _LivePhotos and takes the timestamp of ITS photo."""
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    live = _f("100APPLE/IMG_0001.MOV", T + 5)      # slightly offset mtime
    video = _f("100APPLE/IMG_0002.MOV", T + 100)   # real video, no photo
    lay = ArchiveLayout()
    lay.begin([photo, live, video])
    assert lay.target_for(photo) == "2024/2024-08/2024-08-15 14-30-22.heic"
    assert lay.target_for(live) == \
        "_LivePhotos/2024/2024-08/2024-08-15 14-30-22.mov"
    assert lay.target_for(video) == "2024/2024-08/2024-08-15 14-32-02.mov"


def test_archive_aae_follows_its_photo():
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    aae = _f("100APPLE/IMG_0001.AAE", T + 86400 * 30)   # edited a month later
    lay = ArchiveLayout()
    lay.begin([photo, aae])
    # The AAE stays in the photo's monthly folder, with its timestamp
    assert lay.target_for(aae) == "2024/2024-08/2024-08-15 14-30-22.aae"


def test_archive_pairing_is_limited_to_the_same_folder():
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    other = _f("101APPLE/IMG_0001.MOV", T + 5)   # same name, ANOTHER folder
    lay = ArchiveLayout()
    lay.begin([photo, other])
    assert lay.target_for(other).startswith("2024/")       # not _LivePhotos


def test_factory_and_ids():
    assert make_layout("mirror").id == "mirror"
    assert make_layout("date").id == "date"
    assert make_layout("date", screenshots_apart=True).id == "date+screenshots"
    assert make_layout("archive").id == "archive"
    for lid in ("mirror", "date", "date+screenshots", "archive"):
        assert layout_from_id(lid).id == lid
        assert label_for(lid) != lid   # a human label is defined
    with pytest.raises(ValueError):
        make_layout("unknown")


def test_lock_error_message_is_actionable():
    e = LayoutLockedError("mirror", "archive")
    assert "frozen" in str(e) and "Mirror" in str(e)
