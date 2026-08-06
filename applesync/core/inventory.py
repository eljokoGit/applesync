"""Inventaire avec défense contre la troncature silencieuse.

Le contrat DeviceSession promet qu'une énumération terminée sans exception est
complète. On ne s'y fie pas : l'inventaire énumère DEUX fois et compare les
ensembles (chemin, taille, mtime). Le moindre écart → InventoryMismatchError
avec la liste nominative des différences. C'est exactement le défaut MTP
observé (164 puis 124 puis 185 dossiers sans erreur) qu'on refuse de laisser
passer.

Un inventaire qui échoue n'existe pas : pas d'objet partiel, une exception.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from applesync.device.base import DeviceSession, RemoteFile


class InventoryError(Exception):
    """Inventaire impossible ou non fiable — on s'arrête."""


class InventoryMismatchError(InventoryError):
    """Les deux énumérations divergent : énumération non fiable.

    `only_first` / `only_second` : chemins vus dans une seule des deux passes.
    """

    def __init__(self, only_first: list[str], only_second: list[str]):
        self.only_first = sorted(only_first)
        self.only_second = sorted(only_second)
        preview = ", ".join((self.only_first + self.only_second)[:5])
        super().__init__(
            f"Énumérations divergentes : {len(self.only_first)} fichier(s) vus "
            f"uniquement à la 1re passe, {len(self.only_second)} uniquement à "
            f"la 2de (ex. : {preview}). Inventaire NON fiable, aucune copie "
            f"ne sera lancée."
        )


class InventoryCancelledError(InventoryError):
    """Interruption demandée par l'utilisateur pendant l'inventaire."""


@dataclass(frozen=True)
class Inventory:
    """Inventaire complet et vérifié (double énumération concordante)."""

    device_udid: str
    taken_at: float                      # epoch
    files: tuple[RemoteFile, ...]        # triés par chemin
    duration_s: float
    double_checked: bool

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def fingerprint(self) -> str:
        """Empreinte stable de l'inventaire : sha256 des lignes (path, size, mtime).

        Deux inventaires identiques → même empreinte. Utilisé par le test de
        stabilité (critère de réussite : 3 inventaires identiques).
        """
        import hashlib

        h = hashlib.sha256()
        for f in self.files:
            h.update(f"{f.path}\x00{f.size}\x00{f.mtime}\n".encode())
        return h.hexdigest()


ProgressCb = Callable[[int, str], None]  # (n_fichiers_vus, phase)


def _enumerate_once(
    session: DeviceSession,
    phase: str,
    progress_cb: Optional[ProgressCb],
    cancel: Optional[Callable[[], bool]],
) -> dict[str, RemoteFile]:
    seen: dict[str, RemoteFile] = {}
    for f in session.walk_dcim():
        if cancel is not None and cancel():
            raise InventoryCancelledError("inventaire interrompu par l'utilisateur")
        if f.path in seen:
            # Un même chemin livré deux fois est aussi un signe d'énumération
            # malade : on refuse.
            raise InventoryError(f"chemin énuméré en double : {f.path}")
        seen[f.path] = f
        if progress_cb is not None and len(seen) % 100 == 0:
            progress_cb(len(seen), phase)
    if progress_cb is not None:
        progress_cb(len(seen), phase)
    return seen


def take_inventory(
    session: DeviceSession,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[Callable[[], bool]] = None,
    double_check: bool = True,
) -> Inventory:
    """Inventaire complet, vérifié par double énumération.

    Toute erreur d'appareil se propage telle quelle (échec bruyant).
    `double_check=False` n'existe que pour les mesures de durée ; la synchro
    passe toujours par double_check=True.
    """
    start = time.time()
    udid = session.device_info().udid

    first = _enumerate_once(session, "énumération 1/2" if double_check else "énumération", progress_cb, cancel)

    if double_check:
        second = _enumerate_once(session, "énumération 2/2", progress_cb, cancel)
        only_first = [p for p in first if p not in second]
        only_second = [p for p in second if p not in first]
        # Divergence de métadonnées sur un même chemin = divergence aussi.
        for p in first:
            if p in second and first[p].identity != second[p].identity:
                only_first.append(p)
                only_second.append(p)
        if only_first or only_second:
            raise InventoryMismatchError(only_first, only_second)

    files = tuple(sorted(first.values(), key=lambda f: f.path))
    return Inventory(
        device_udid=udid,
        taken_at=start,
        files=files,
        duration_s=time.time() - start,
        double_checked=double_check,
    )
