"""Reading the EXIF capture date of a LOCAL file.

Used by the "archive" layout AFTER the copy (the file is on disk by then: a
header read, negligible cost). Never used for incremental identity, which
remains (path, size, mtime).

Pillow reads JPEG EXIF natively; for HEIC it fails cleanly (no decoder) and we
fall back to mtime — harmless, since HEIC files are the photos taken by the
device itself, whose mtime is reliable. The problem this module solves is old
JPGs whose mtime is a bulk-import date rather than a capture date.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

_DATETIME_ORIGINAL = 36867      # Exif IFD: DateTimeOriginal
_DATETIME = 306                 # IFD0: DateTime (fallback)
_EXIF_IFD = 0x8769


def exif_timestamp(path: Path) -> Optional[int]:
    """Local-time epoch of the EXIF capture date, or None if not found.

    Never raises: any failure (unreadable format, no EXIF, absurd date)
    returns None and mtime takes over.
    """
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            raw = None
            try:
                raw = exif.get_ifd(_EXIF_IFD).get(_DATETIME_ORIGINAL)
            except Exception:
                raw = None
            if not raw:
                raw = exif.get(_DATETIME)
        if not raw or not isinstance(raw, str):
            return None
        st = time.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S")
        if st.tm_year < 1990 or st.tm_year > 2100:
            return None
        return int(time.mktime(st))
    except Exception:
        return None
