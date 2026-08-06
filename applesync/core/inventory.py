"""Inventory, with a defence against silent truncation.

The DeviceSession contract promises that an enumeration finishing without an
exception is complete. We do not take its word for it: the inventory
enumerates TWICE and compares the (path, size, mtime) sets. The slightest
difference raises InventoryMismatchError with the offending names. This is
exactly the MTP defect this project exists to refuse — enumerations returning
164, then 124, then 185 folders without ever raising.

An inventory that fails does not exist: no partial object, an exception.

Known limit: both passes happen inside the same device session, so they catch
non-deterministic truncation, not a truncation that would be stable for the
whole session. The defences against that live outside: the stability check
(one fresh session per pass, see core/stability.py) and comparing the count
against what the device itself reports.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from applesync.device.base import DeviceSession, RemoteFile


class InventoryError(Exception):
    """Inventory impossible or untrustworthy — we stop."""


class InventoryMismatchError(InventoryError):
    """The two enumerations disagree: enumeration is not trustworthy.

    `only_first` / `only_second`: paths seen in only one of the two passes.
    """

    def __init__(self, only_first: list[str], only_second: list[str]):
        self.only_first = sorted(only_first)
        self.only_second = sorted(only_second)
        preview = ", ".join((self.only_first + self.only_second)[:5])
        super().__init__(
            f"Enumerations disagree: {len(self.only_first)} file(s) seen only "
            f"in pass 1, {len(self.only_second)} only in pass 2 "
            f"(e.g. {preview}). Inventory NOT trustworthy, no copy will start."
        )


class InventoryCancelledError(InventoryError):
    """The user asked to stop during the inventory."""


@dataclass(frozen=True)
class Inventory:
    """A complete, verified inventory (two matching enumerations)."""

    device_udid: str
    taken_at: float                      # epoch
    files: tuple[RemoteFile, ...]        # sorted by path
    duration_s: float
    double_checked: bool

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def fingerprint(self) -> str:
        """Stable fingerprint: sha256 over the (path, size, mtime) lines.

        Two identical inventories produce the same fingerprint. Used by the
        stability check (success criterion: three identical inventories).
        """
        import hashlib

        h = hashlib.sha256()
        for f in self.files:
            h.update(f"{f.path}\x00{f.size}\x00{f.mtime}\n".encode())
        return h.hexdigest()


ProgressCb = Callable[[int, str], None]  # (files_seen, phase)


def _enumerate_once(
    session: DeviceSession,
    phase: str,
    progress_cb: Optional[ProgressCb],
    cancel: Optional[Callable[[], bool]],
) -> dict[str, RemoteFile]:
    seen: dict[str, RemoteFile] = {}
    for f in session.walk_dcim():
        if cancel is not None and cancel():
            raise InventoryCancelledError("inventory cancelled by the user")
        if f.path in seen:
            # The same path delivered twice is another symptom of a sick
            # enumeration: refuse it.
            raise InventoryError(f"path enumerated twice: {f.path}")
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
    """Full inventory, verified by double enumeration.

    Any device error propagates as-is (fail loudly). `double_check=False`
    exists only for timing measurements; synchronisation always runs with
    double_check=True.
    """
    start = time.time()
    udid = session.device_info().udid

    first = _enumerate_once(
        session, "pass 1/2" if double_check else "enumerating", progress_cb, cancel
    )

    if double_check:
        second = _enumerate_once(session, "pass 2/2", progress_cb, cancel)
        only_first = [p for p in first if p not in second]
        only_second = [p for p in second if p not in first]
        # Differing metadata on the same path counts as a divergence too.
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
