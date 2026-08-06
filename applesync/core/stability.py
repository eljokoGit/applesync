"""Test de stabilité : le critère de réussite du projet, mesuré.

Trois inventaires successifs — avec débranchement/rebranchement entre chacun —
doivent renvoyer exactement le même nombre de fichiers, le même volume et la
même empreinte. Ce module exécute la mesure et rend un verdict nominatif.

Le débranchement est demandé à l'utilisateur via `wait_between_rounds`
(l'UI attend la disparition puis la réapparition de l'appareil) ; les tests
utilisent un callback qui reconnecte le simulateur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from applesync.core.inventory import Inventory, ProgressCb, take_inventory
from applesync.device.base import DeviceBackend


@dataclass(frozen=True)
class StabilityRound:
    index: int
    count: int
    total_bytes: int
    fingerprint: str
    duration_s: float


@dataclass
class StabilityResult:
    rounds: list[StabilityRound] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)   # écarts nominatifs entre passes

    @property
    def stable(self) -> bool:
        if len(self.rounds) < 2:
            return False
        first = self.rounds[0]
        return not self.diffs and all(
            r.count == first.count
            and r.total_bytes == first.total_bytes
            and r.fingerprint == first.fingerprint
            for r in self.rounds
        )

    def verdict(self) -> str:
        if self.stable:
            r = self.rounds[0]
            return (
                f"STABLE : {len(self.rounds)} inventaires identiques — "
                f"{r.count} fichiers, {r.total_bytes} octets, "
                f"empreinte {r.fingerprint[:16]}…"
            )
        lines = ["INSTABLE : les inventaires divergent."]
        for r in self.rounds:
            lines.append(
                f"  passe {r.index}: {r.count} fichiers, {r.total_bytes} octets, "
                f"empreinte {r.fingerprint[:16]}…"
            )
        lines.extend(f"  écart : {d}" for d in self.diffs[:50])
        if len(self.diffs) > 50:
            lines.append(f"  … et {len(self.diffs) - 50} autres écarts")
        return "\n".join(lines)


def run_stability_check(
    backend: DeviceBackend,
    udid: str,
    rounds: int = 3,
    wait_between_rounds: Optional[Callable[[int], None]] = None,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> StabilityResult:
    """Exécute `rounds` inventaires complets (chacun déjà à double énumération).

    `wait_between_rounds(i)` est appelé entre les passes : c'est là que l'UI
    demande le débranchement/rebranchement et attend l'appareil.
    """
    result = StabilityResult()
    inventories: list[Inventory] = []

    for i in range(1, rounds + 1):
        if i > 1 and wait_between_rounds is not None:
            wait_between_rounds(i)
        session = backend.connect(udid)
        try:
            inv = take_inventory(session, progress_cb=progress_cb, cancel=cancel)
        finally:
            session.close()
        inventories.append(inv)
        result.rounds.append(
            StabilityRound(
                index=i,
                count=inv.count,
                total_bytes=inv.total_bytes,
                fingerprint=inv.fingerprint(),
                duration_s=inv.duration_s,
            )
        )

    # Écarts nominatifs entre la première passe et chacune des suivantes.
    if inventories:
        ref = {f.path: f for f in inventories[0].files}
        for round_no, inv in enumerate(inventories[1:], start=2):
            cur = {f.path: f for f in inv.files}
            for p in sorted(set(ref) - set(cur)):
                result.diffs.append(f"{p} : vu passe 1, absent passe {round_no}")
            for p in sorted(set(cur) - set(ref)):
                result.diffs.append(f"{p} : absent passe 1, vu passe {round_no}")
            for p in sorted(set(ref) & set(cur)):
                if ref[p].identity != cur[p].identity:
                    result.diffs.append(f"{p} : métadonnées différentes entre passes 1 et {round_no}")

    return result
