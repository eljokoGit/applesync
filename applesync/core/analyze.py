"""Ventilation de l'inventaire : mois (dossier YYYYMM) × extension.

Produite à chaque inventaire (mode « Inventorier » seul compris), en CSV dans
`<destination>/.applesync/rapports/`. Raison d'être : comparer le contenu réel
du DCIM à une sauvegarde historique et qualifier un écart — par exemple savoir
si des fichiers manquants sont des annexes (.MOV de Live Photos, .AAE) ou de
vraies photos (.HEIC/.JPG).

CSV : séparateur « ; » (Excel français), UTF-8 avec BOM, dernière ligne TOTAL.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from applesync.core.inventory import Inventory

_MONTH_RE = re.compile(r"^(\d{6})")


def breakdown_rows(inventory: Inventory) -> list[tuple[str, str, int, int]]:
    """Lignes (mois, extension, nb_fichiers, octets), triées.

    `mois` = 6 premiers chiffres du dossier de premier niveau (YYYYMM) quand il
    en a la forme, sinon le nom du dossier tel quel (ex. « 100APPLE ») — la
    colonne reste agrégeable dans tous les cas.
    """
    agg: dict[tuple[str, str], list[int]] = {}
    for f in inventory.files:
        folder = f.path.split("/", 1)[0] if "/" in f.path else "(racine)"
        m = _MONTH_RE.match(folder)
        mois = m.group(1) if m else folder
        name = f.name
        ext = name.rsplit(".", 1)[-1].upper() if "." in name else "(sans extension)"
        cell = agg.setdefault((mois, ext), [0, 0])
        cell[0] += 1
        cell[1] += f.size
    return [(m, e, c, b) for (m, e), (c, b) in sorted(agg.items())]


def write_breakdown_csv(inventory: Inventory, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = breakdown_rows(inventory)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["mois", "extension", "fichiers", "octets"])
        w.writerows(rows)
        w.writerow(["TOTAL", "", inventory.count, inventory.total_bytes])
    return path
