"""Vérification de la destination : relecture réelle, écarts NOMINATIFS.

Après copie (ou à la demande), on relit la destination et on compare fichier
par fichier à l'inventaire source, via le manifeste :

- pour chaque fichier de l'inventaire : le fichier local attendu existe-t-il ?
  sa taille correspond-elle ? et, en mode approfondi, son SHA-256 relu
  correspond-il à celui calculé pendant la copie ?
- sortie : des LISTES DE NOMS (manquants, tailles fausses, hachages faux,
  non couverts par le manifeste), jamais un pourcentage seul.

Le mode approfondi relit physiquement chaque octet du disque : c'est lui qui
autorise à dire « je peux supprimer les originaux du téléphone ».
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from applesync.core.inventory import Inventory
from applesync.core.manifest import Manifest
from applesync.device.base import RemoteFile

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Discrepancy:
    source_path: str
    local_path: str
    kind: str        # absent_du_manifeste | fichier_manquant | taille | sha256
    detail: str


@dataclass
class VerificationReport:
    checked_count: int = 0
    ok_count: int = 0
    hashed_count: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.discrepancies

    def names(self) -> list[str]:
        return [d.source_path for d in self.discrepancies]


ProgressCb = Callable[[int, int, str], None]   # (n_faits, n_total, fichier_courant)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_against_inventory(
    inventory_files: Iterable[RemoteFile],
    manifest: Manifest,
    dest_root: Path,
    deep_hash: bool = True,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> VerificationReport:
    """Compare la destination à l'inventaire source, fichier par fichier.

    `deep_hash=True` relit chaque fichier local et compare son SHA-256 au
    manifeste. `False` se limite à existence + taille (contrôle rapide).
    Une interruption (`cancel`) lève : un rapport partiel n'existe pas.
    """
    dest_root = Path(dest_root)
    files = list(inventory_files)
    report = VerificationReport()

    for i, f in enumerate(files, 1):
        if cancel is not None and cancel():
            raise InterruptedError("vérification interrompue — aucun rapport partiel")
        if progress_cb is not None:
            progress_cb(i, len(files), f.path)

        entry = manifest.lookup(f.identity)
        report.checked_count += 1

        if entry is None:
            report.discrepancies.append(
                Discrepancy(f.path, "", "absent_du_manifeste",
                            "fichier de l'inventaire jamais enregistré comme copié")
            )
            continue

        local = dest_root / entry.local_path
        if not local.exists():
            report.discrepancies.append(
                Discrepancy(f.path, entry.local_path, "fichier_manquant",
                            f"attendu à {entry.local_path}, absent du disque")
            )
            continue

        actual_size = local.stat().st_size
        if actual_size != f.size:
            report.discrepancies.append(
                Discrepancy(f.path, entry.local_path, "taille",
                            f"disque : {actual_size} o, source : {f.size} o")
            )
            continue

        if deep_hash:
            actual_sha = _sha256_of(local)
            report.hashed_count += 1
            if actual_sha != entry.sha256:
                report.discrepancies.append(
                    Discrepancy(f.path, entry.local_path, "sha256",
                                f"disque : {actual_sha[:16]}…, "
                                f"manifeste : {entry.sha256[:16]}…")
                )
                continue

        report.ok_count += 1

    return report
