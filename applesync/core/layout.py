"""Destination layout strategies.

- "mirror" (default): the device tree as-is.
- "date": YYYY/YYYY-MM/ based on the file mtime, original names kept. The
  "screenshots apart" option puts PNG files in a Screenshots/ subfolder. MP4
  files (videos received through messaging, downloads, screen recordings)
  stay in the monthly flow, treated like any other video.
- "archive": dated layout with timestamp renaming —
    YYYY/YYYY-MM/YYYY-MM-DD HH-MM-SS.ext
    _LivePhotos/YYYY/YYYY-MM/…ext        (video part of Live Photos)
  A Live Photo component is a .MOV whose twin photo shares its name in the
  same device folder (IMG_1234.HEIC + IMG_1234.MOV); it is dated and named
  after ITS photo. Sidecar .AAE files follow their photo (same timestamp,
  same monthly folder). Original extensions are kept, lowercased — never any
  conversion; a collision on the same second (burst) resolves to .~2, .~3…
  — never an overwrite.

Renaming uses the local time of the PC. In the "archive" layout the date
comes from EXIF, read after the copy (see engine.py), with mtime as fallback;
incremental identity, however, remains (path, size, mtime).

The layout is FROZEN per destination on the first synchronisation (recorded
in the manifest): changing it would require re-copying or moving everything —
an explicit refusal beats silent duplicates.

Verification does not depend on the layout: the manifest stores the real
local path of every file, and the re-read follows it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import Iterable, Optional

from applesync.device.base import RemoteFile

SCREENSHOT_EXTENSIONS = {"PNG"}
PHOTO_EXTENSIONS = {"HEIC", "JPG", "JPEG", "PNG", "DNG", "WEBP", "GIF",
                    "TIF", "TIFF", "BMP"}

# iCloud shared albums (inventory prefix PhotoCloudSharingData/) hold other
# people's photos: in dated layouts they go to their own folder rather than
# being mixed into the personal archive.
SHARED_PREFIX = "PhotoCloudSharingData/"
SHARED_DIRNAME = "_SharedAlbums"


def shared_target(path: str) -> Optional[str]:
    """Separate target for a shared-album item, otherwise None."""
    if path.startswith(SHARED_PREFIX):
        return f"{SHARED_DIRNAME}/{path[len(SHARED_PREFIX):]}"
    return None


def _ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].upper() if "." in name else ""


def _dir_stem(path: str) -> tuple[str, str]:
    p = PurePosixPath(path)
    return (str(p.parent), p.stem)


class LayoutLockedError(Exception):
    """The destination already has a layout, different from the one asked."""

    def __init__(self, locked_id: str, requested_id: str):
        self.locked_id = locked_id
        self.requested_id = requested_id
        super().__init__(
            f"This destination was built with the \"{label_for(locked_id)}\" "
            f"layout; the requested option is \"{label_for(requested_id)}\". "
            f"The layout is frozen by the first synchronisation: switch back "
            f"to the original option, or pick another destination."
        )


class Layout(ABC):
    id: str = ""
    # Folder where content duplicates detected during the copy are filed
    # (None = no filing, duplicates stay in the normal flow).
    duplicates_dir: Optional[str] = None
    # True: the final target is decided AFTER the copy (EXIF date read from
    # the local file). The plan then only assigns a staging location.
    finalize_dating: bool = False

    def begin(self, files: Iterable[RemoteFile]) -> None:
        """Called once with the full inventory before any target_for (enables
        Live Photo / AAE pairing). Default: nothing."""

    @abstractmethod
    def target_for(self, f: RemoteFile) -> str:
        """Relative local path (/ separators) where this file must live."""


class MirrorLayout(Layout):
    id = "mirror"

    def target_for(self, f: RemoteFile) -> str:
        return str(PurePosixPath(f.path))


class DateLayout(Layout):
    def __init__(self, screenshots_apart: bool = False):
        self.screenshots_apart = screenshots_apart
        self.id = "date+screenshots" if screenshots_apart else "date"

    def target_for(self, f: RemoteFile) -> str:
        part = shared_target(f.path)
        if part is not None:
            return part
        t = time.localtime(f.mtime)
        base = f"{t.tm_year:04d}/{t.tm_year:04d}-{t.tm_mon:02d}"
        name = f.name
        if self.screenshots_apart:
            ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
            if ext in SCREENSHOT_EXTENSIONS:
                base += "/Screenshots"
        return f"{base}/{name}"


class ArchiveLayout(Layout):
    """Dated layout with timestamp renaming — see the module docstring.

    CONTENT duplicates (a SHA-256 already present in the manifest) are filed
    under _Duplicates/ during the sync, keeping the structure
    (_Duplicates/YYYY/YYYY-MM/…). The first copy encountered stays in the
    normal flow; only the surplus copies are moved. Detection happens at copy
    time (that is where the hash appears): nothing is ever overwritten or
    deleted, only filed elsewhere.
    """

    id = "archive"
    duplicates_dir = "_Duplicates"
    finalize_dating = True

    def __init__(self) -> None:
        self._photo_by_key: dict[tuple[str, str], RemoteFile] = {}

    def begin(self, files: Iterable[RemoteFile]) -> None:
        self._photo_by_key = {
            _dir_stem(f.path): f
            for f in files
            if _ext_of(f.path) in PHOTO_EXTENSIONS
        }

    def paired_photo(self, f: RemoteFile) -> Optional[RemoteFile]:
        """The twin photo (same folder, same stem) of a MOV/AAE, or None."""
        return self._photo_by_key.get(_dir_stem(f.path))

    def dated_target(self, f: RemoteFile, ts: int, as_live: bool = False) -> str:
        """Target for `f` dated at epoch `ts` (EXIF or mtime, caller's call)."""
        ext = _ext_of(f.path)
        t = time.localtime(ts)
        stamp = (f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d} "
                 f"{t.tm_hour:02d}-{t.tm_min:02d}-{t.tm_sec:02d}")
        month_dir = f"{t.tm_year:04d}/{t.tm_year:04d}-{t.tm_mon:02d}"
        name = f"{stamp}.{ext.lower()}" if ext else stamp
        if as_live:
            return f"_LivePhotos/{month_dir}/{name}"
        return f"{month_dir}/{name}"

    def target_for(self, f: RemoteFile) -> str:
        """Target based on mtime alone (no EXIF) — a forecast and a fallback;
        the final target is computed after the copy (finalize_dating)."""
        part = shared_target(f.path)
        if part is not None:
            return part
        ext = _ext_of(f.path)
        paired = self.paired_photo(f) if ext in ("MOV", "AAE") else None
        ref = paired if paired is not None else f
        return self.dated_target(f, ref.mtime, as_live=(ext == "MOV" and paired is not None))


def make_layout(kind: str, screenshots_apart: bool = False) -> Layout:
    """`kind`: "mirror", "date" or "archive"."""
    if kind == "mirror":
        return MirrorLayout()
    if kind == "date":
        return DateLayout(screenshots_apart=screenshots_apart)
    if kind == "archive":
        return ArchiveLayout()
    raise ValueError(f"unknown layout: {kind}")


def layout_from_id(layout_id: str) -> Layout:
    """Rebuild a strategy from the identifier frozen in the manifest."""
    if layout_id == "mirror":
        return MirrorLayout()
    if layout_id == "date":
        return DateLayout(False)
    if layout_id == "date+screenshots":
        return DateLayout(True)
    if layout_id == "archive":
        return ArchiveLayout()
    raise ValueError(f"unknown layout: {layout_id}")


def label_for(layout_id: str) -> str:
    return {
        "mirror": "Mirror of the device tree",
        "date": "By date (YYYY/YYYY-MM)",
        "date+screenshots": "By date, screenshots apart",
        "archive": "Archive (date renaming, _LivePhotos)",
    }.get(layout_id, layout_id)
