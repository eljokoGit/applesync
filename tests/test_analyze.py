"""Month x extension breakdown: exact totals, readable CSV."""

import csv

from applesync.core.analyze import breakdown_rows, write_breakdown_csv
from applesync.core.engine import SyncEngine
from applesync.core.inventory import take_inventory


def _inventory(backend):
    with backend.connect(backend.INFO.udid) as s:
        return take_inventory(s)


def test_breakdown_totals_are_exact(backend):
    inv = _inventory(backend)
    rows = breakdown_rows(inv)
    assert sum(r[2] for r in rows) == inv.count
    assert sum(r[3] for r in rows) == inv.total_bytes
    # Months as YYYYMM, extracted from the YYYYMM_a folders
    assert all(len(r[0]) == 6 and r[0].isdigit() for r in rows)
    # Both simulator extensions are present
    assert {r[1] for r in rows} == {"HEIC", "MOV"}


def test_csv_is_readable_and_carries_a_total(backend, tmp_path):
    inv = _inventory(backend)
    path = write_breakdown_csv(inv, tmp_path / "inventory.csv")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0] == ["month", "extension", "files", "bytes"]
    total = rows[-1]
    assert total[0] == "TOTAL"
    assert int(total[2]) == inv.count
    assert int(total[3]) == inv.total_bytes
    # Data rows sum to the TOTAL row
    assert sum(int(r[2]) for r in rows[1:-1]) == inv.count


def test_prepare_exports_the_csv(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    assert prepared.breakdown_csv is not None
    assert prepared.breakdown_csv.exists()
    assert prepared.breakdown_csv.parent == dest / ".applesync" / "reports"
    # Taking an inventory writes NOTHING into the backup mirror
    mirror_files = [
        p for p in dest.rglob("*")
        if p.is_file() and ".applesync" not in p.parts
    ]
    assert mirror_files == []
