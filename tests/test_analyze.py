"""Ventilation mois × extension : totaux exacts, CSV relisible."""

import csv

from applesync.core.analyze import breakdown_rows, write_breakdown_csv
from applesync.core.engine import SyncEngine
from applesync.core.inventory import take_inventory


def _inventory(backend):
    with backend.connect(backend.INFO.udid) as s:
        return take_inventory(s)


def test_ventilation_totaux_exacts(backend):
    inv = _inventory(backend)
    rows = breakdown_rows(inv)
    assert sum(r[2] for r in rows) == inv.count
    assert sum(r[3] for r in rows) == inv.total_bytes
    # Mois au format YYYYMM extraits des dossiers YYYYMM_a
    assert all(len(r[0]) == 6 and r[0].isdigit() for r in rows)
    # Les deux extensions du simulateur sont présentes
    assert {r[1] for r in rows} == {"HEIC", "MOV"}


def test_csv_relisible_avec_total(backend, tmp_path):
    inv = _inventory(backend)
    path = write_breakdown_csv(inv, tmp_path / "inventaire.csv")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        lignes = list(csv.reader(fh, delimiter=";"))
    assert lignes[0] == ["mois", "extension", "fichiers", "octets"]
    total = lignes[-1]
    assert total[0] == "TOTAL"
    assert int(total[2]) == inv.count
    assert int(total[3]) == inv.total_bytes
    # Somme des lignes de données = ligne TOTAL
    assert sum(int(l[2]) for l in lignes[1:-1]) == inv.count


def test_prepare_exporte_le_csv(backend, dest):
    engine = SyncEngine(backend, dest)
    prepared = engine.prepare(backend.INFO.udid)
    assert prepared.breakdown_csv is not None
    assert prepared.breakdown_csv.exists()
    assert prepared.breakdown_csv.parent == dest / ".applesync" / "rapports"
    # L'inventaire seul n'écrit RIEN dans le miroir de sauvegarde
    fichiers_miroir = [
        p for p in dest.rglob("*")
        if p.is_file() and ".applesync" not in p.parts
    ]
    assert fichiers_miroir == []
