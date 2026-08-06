"""Inventory breakdown: month (YYYYMM folder) x extension.

Produced on every inventory (including a plain "Inventory" run), as a CSV in
`<destination>/.applesync/reports/`. Its purpose: compare the real content of
the device with an existing backup and qualify a gap — for instance telling
whether missing files are sidecars (Live Photo .MOV, .AAE) or actual photos
(.HEIC/.JPG).

CSV: ";" separator (friendly to European spreadsheets), UTF-8 with BOM, TOTAL
as the last row.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from applesync.core.inventory import Inventory

_MONTH_RE = re.compile(r"^(\d{6})")


def breakdown_rows(inventory: Inventory) -> list[tuple[str, str, int, int]]:
    """Rows of (month, extension, file count, bytes), sorted.

    `month` is the first six digits of the top-level folder (YYYYMM) when it
    has that shape, otherwise the folder name as-is (e.g. "100APPLE") — the
    column stays aggregatable either way.
    """
    agg: dict[tuple[str, str], list[int]] = {}
    for f in inventory.files:
        folder = f.path.split("/", 1)[0] if "/" in f.path else "(root)"
        m = _MONTH_RE.match(folder)
        month = m.group(1) if m else folder
        name = f.name
        ext = name.rsplit(".", 1)[-1].upper() if "." in name else "(no extension)"
        cell = agg.setdefault((month, ext), [0, 0])
        cell[0] += 1
        cell[1] += f.size
    return [(m, e, c, b) for (m, e), (c, b) in sorted(agg.items())]


def write_breakdown_csv(inventory: Inventory, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = breakdown_rows(inventory)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["month", "extension", "files", "bytes"])
        w.writerows(rows)
        w.writerow(["TOTAL", "", inventory.count, inventory.total_bytes])
    return path
