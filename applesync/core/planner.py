"""Plan de synchronisation : delta entre inventaire source et manifeste.

Classement de chaque fichier de l'inventaire :
- `already_synced` : identité (chemin, taille, mtime) déjà au manifeste.
- `to_adopt` : absent du manifeste, mais un fichier local existe au chemin
  cible avec la même taille ET le même mtime (posé par une copie antérieure).
  Cas typique : manifeste perdu ou destination pré-remplie. On hache le
  fichier local et on l'adopte sans re-copier — critère plus fort que le nom.
- `conflicts` : un fichier local existe au chemin cible mais ne correspond
  PAS (taille ou mtime différents). On ne remplace jamais un fichier local :
  la nouvelle version ira sous un nom versionné (IMG_0001.HEIC → IMG_0001.~2.HEIC).
- `to_copy` : tout le reste — à rapatrier.

Et côté disparitions :
- `missing_on_device` : entrées du manifeste absentes de l'inventaire
  (suppression sur l'iPhone). Le fichier local reste ; signalé au rapport.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from applesync.core.layout import Layout, MirrorLayout
from applesync.core.manifest import Manifest, ManifestEntry
from applesync.core.inventory import Inventory
from applesync.device.base import RemoteFile


@dataclass(frozen=True)
class Conflict:
    remote: RemoteFile
    local_path: str          # chemin local (relatif) déjà occupé
    versioned_path: str      # chemin local (relatif) où ira la nouvelle version
    reason: str


@dataclass
class SyncPlan:
    to_copy: list[RemoteFile] = field(default_factory=list)
    to_adopt: list[RemoteFile] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    already_synced: list[RemoteFile] = field(default_factory=list)
    missing_on_device: list[ManifestEntry] = field(default_factory=list)
    targets: dict[str, str] = field(default_factory=dict)   # source path → cible locale

    @property
    def bytes_to_copy(self) -> int:
        return sum(f.size for f in self.to_copy) + sum(
            c.remote.size for c in self.conflicts
        )

    @property
    def files_to_transfer(self) -> list[tuple[RemoteFile, str]]:
        """(fichier source, chemin local relatif cible) pour la phase de copie."""
        out = [(f, self.targets[f.path]) for f in self.to_copy]
        out.extend((c.remote, c.versioned_path) for c in self.conflicts)
        return out


def local_target(source_path: str) -> str:
    """Chemin local relatif cible en disposition miroir (historique)."""
    return str(PurePosixPath(source_path))


def staging_target(f: RemoteFile) -> str:
    """Emplacement de transit d'un fichier en attente de datation finale.

    Déterministe par chemin source (la reprise retrouve son .part d'un run à
    l'autre) et confiné sous .applesync/transit/ : aucune collision possible
    avec les cibles définitives."""
    import hashlib

    digest = hashlib.sha1(f.path.encode("utf-8")).hexdigest()[:24]
    ext = f.path.rsplit(".", 1)[-1].lower() if "." in f.path else "bin"
    return f".applesync/transit/{digest}.{ext}"


def versioned_target(dest_root: Path, target_rel: str, taken: set[str]) -> str:
    """Premier chemin versionné libre (ni sur disque, ni déjà promis au plan) :
    IMG_0001.HEIC → IMG_0001.~2.HEIC, .~3…"""
    p = PurePosixPath(target_rel)
    stem, suffix = p.stem, p.suffix
    n = 2
    while True:
        candidate = str(p.parent / f"{stem}.~{n}{suffix}")
        if candidate not in taken and not (dest_root / candidate).exists():
            return candidate
        n += 1


def build_plan(
    inventory: Inventory,
    manifest: Manifest,
    dest_root: Path,
    layout: Optional[Layout] = None,
) -> SyncPlan:
    layout = layout or MirrorLayout()
    layout.begin(inventory.files)
    plan = SyncPlan()
    dest_root = Path(dest_root)
    inventory_paths = set()
    assigned: set[str] = set()   # cibles promises par ce plan (anti-collision)

    for f in inventory.files:
        inventory_paths.add(f.path)
        entry = manifest.lookup(f.identity)
        if entry is not None:
            plan.already_synced.append(f)
            plan.targets[f.path] = entry.local_path
            continue

        if layout.finalize_dating:
            # La cible définitive sera décidée après copie (EXIF lu en local).
            # Le plan n'assigne qu'un emplacement de TRANSIT, déterministe par
            # fichier source (reprise à l'octet près conservée), dans un espace
            # qui ne peut jamais entrer en collision avec les cibles finales.
            plan.to_copy.append(f)
            plan.targets[f.path] = staging_target(f)
            continue

        target_rel = layout.target_for(f)
        target_abs = dest_root / target_rel
        if target_abs.exists():
            st = target_abs.stat()
            if st.st_size == f.size and int(st.st_mtime) == f.mtime:
                plan.to_adopt.append(f)
                plan.targets[f.path] = target_rel
                assigned.add(target_rel)
            else:
                versioned = versioned_target(dest_root, target_rel, assigned)
                plan.conflicts.append(
                    Conflict(
                        remote=f,
                        local_path=target_rel,
                        versioned_path=versioned,
                        reason=(
                            f"fichier local présent avec taille/mtime différents "
                            f"(local : {st.st_size} o, mtime {int(st.st_mtime)} ; "
                            f"iPhone : {f.size} o, mtime {f.mtime})"
                        ),
                    )
                )
                plan.targets[f.path] = versioned
                assigned.add(versioned)
        elif target_rel in assigned:
            # Deux fichiers source différents visent la même cible (possible en
            # disposition par date : même nom, même mois). Le second est
            # versionné dès le plan — jamais d'écrasement, jamais d'échec tardif.
            versioned = versioned_target(dest_root, target_rel, assigned)
            plan.conflicts.append(
                Conflict(
                    remote=f,
                    local_path=target_rel,
                    versioned_path=versioned,
                    reason="collision de nom dans le plan (autre fichier source "
                           "visant la même cible)",
                )
            )
            plan.targets[f.path] = versioned
            assigned.add(versioned)
        else:
            plan.to_copy.append(f)
            plan.targets[f.path] = target_rel
            assigned.add(target_rel)

    # Suppressions côté iPhone : au manifeste mais plus dans l'inventaire.
    seen_identities = {f.identity for f in inventory.files}
    for entry in manifest.all_entries():
        if entry.source_path not in inventory_paths:
            plan.missing_on_device.append(entry)
        elif entry.identity not in seen_identities:
            # Le chemin existe encore mais avec une autre identité : l'ancienne
            # version n'est plus sur le téléphone. Signalé également.
            plan.missing_on_device.append(entry)

    return plan
