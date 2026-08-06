"""Destination verification: real re-read, discrepancies BY NAME.

After a copy (or on demand) the destination is read back and compared file by
file to the source inventory, through the manifest:

- for every inventory file: does the expected local file exist? does its size
  match? and, in deep mode, does its re-read SHA-256 match the one computed
  during the copy?
- output: LISTS OF NAMES (missing, wrong size, wrong hash, not covered by the
  manifest), never a bare percentage.

Deep mode physically re-reads every byte from disk: that is what earns the
right to say "I can delete the originals from the phone".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from applesync.core.manifest import Manifest
from applesync.device.base import RemoteFile

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Discrepancy:
    source_path: str
    local_path: str
    kind: str        # not_in_manifest | file_missing | size | sha256
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


ProgressCb = Callable[[int, int, str], None]   # (done, total, current file)


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
    """Compare the destination to the source inventory, file by file.

    `deep_hash=True` re-reads every local file and compares its SHA-256 to the
    manifest. `False` checks existence and size only (quick check).
    An interruption (`cancel`) raises: a partial report does not exist.
    """
    dest_root = Path(dest_root)
    files = list(inventory_files)
    report = VerificationReport()

    for i, f in enumerate(files, 1):
        if cancel is not None and cancel():
            raise InterruptedError("verification interrupted — no partial report")
        if progress_cb is not None:
            progress_cb(i, len(files), f.path)

        entry = manifest.lookup(f.identity)
        report.checked_count += 1

        if entry is None:
            report.discrepancies.append(
                Discrepancy(f.path, "", "not_in_manifest",
                            "inventory file never recorded as copied")
            )
            continue

        local = dest_root / entry.local_path
        if not local.exists():
            report.discrepancies.append(
                Discrepancy(f.path, entry.local_path, "file_missing",
                            f"expected at {entry.local_path}, absent from disk")
            )
            continue

        actual_size = local.stat().st_size
        if actual_size != f.size:
            report.discrepancies.append(
                Discrepancy(f.path, entry.local_path, "size",
                            f"disk: {actual_size} B, source: {f.size} B")
            )
            continue

        if deep_hash:
            actual_sha = _sha256_of(local)
            report.hashed_count += 1
            if actual_sha != entry.sha256:
                report.discrepancies.append(
                    Discrepancy(f.path, entry.local_path, "sha256",
                                f"disk: {actual_sha[:16]}…, "
                                f"manifest: {entry.sha256[:16]}…")
                )
                continue

        report.ok_count += 1

    return report
