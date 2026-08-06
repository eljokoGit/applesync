"""Stability check: the project's success criterion, measured.

Three successive inventories — unplugging and replugging the device between
each — must return exactly the same file count, the same volume and the same
fingerprint. This module runs the measurement and returns a verdict naming
every divergence.

The unplug is requested from the user through `wait_between_rounds` (the UI
waits for the device to disappear and come back); tests pass a callback that
simply reconnects the simulator.
"""

from __future__ import annotations

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
    diffs: list[str] = field(default_factory=list)   # named differences between passes

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
                f"STABLE: {len(self.rounds)} identical inventories — "
                f"{r.count} files, {r.total_bytes} bytes, "
                f"fingerprint {r.fingerprint[:16]}…"
            )
        lines = ["UNSTABLE: the inventories diverge."]
        for r in self.rounds:
            lines.append(
                f"  pass {r.index}: {r.count} files, {r.total_bytes} bytes, "
                f"fingerprint {r.fingerprint[:16]}…"
            )
        lines.extend(f"  difference: {d}" for d in self.diffs[:50])
        if len(self.diffs) > 50:
            lines.append(f"  … and {len(self.diffs) - 50} more differences")
        return "\n".join(lines)


def run_stability_check(
    backend: DeviceBackend,
    udid: str,
    rounds: int = 3,
    wait_between_rounds: Optional[Callable[[int], None]] = None,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> StabilityResult:
    """Run `rounds` full inventories (each already double-enumerated).

    `wait_between_rounds(i)` is called between passes: that is where the UI
    asks for the unplug/replug and waits for the device.
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

    # Named differences between the first pass and each of the following ones.
    if inventories:
        ref = {f.path: f for f in inventories[0].files}
        for round_no, inv in enumerate(inventories[1:], start=2):
            cur = {f.path: f for f in inv.files}
            for p in sorted(set(ref) - set(cur)):
                result.diffs.append(f"{p}: seen in pass 1, absent in pass {round_no}")
            for p in sorted(set(cur) - set(ref)):
                result.diffs.append(f"{p}: absent in pass 1, seen in pass {round_no}")
            for p in sorted(set(ref) & set(cur)):
                if ref[p].identity != cur[p].identity:
                    result.diffs.append(
                        f"{p}: metadata differ between passes 1 and {round_no}"
                    )

    return result
