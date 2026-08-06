"""Copy engine: download to .part, exact resume, SHA-256 on the fly.

Guarantees:
- A file appears under its final name ONLY when complete: written to
  `<target>.part` plus a `.part.meta.json` sidecar, then fsync, size check and
  os.replace (atomic) to the final name. Never a partial file in disguise.
- Resume: on restart, if the .part exists and the sidecar matches the current
  source identity (path, size, mtime), the local partial file is re-hashed and
  reading continues at the exact offset (seek on the device side). If the
  identity changed, that file starts over.
- The SHA-256 covers EVERY written byte (including the re-read partial): the
  final hash is the hash of the complete file, resumed or not.
- User interruption: stop at a block boundary, .part kept, state picked up
  as-is on the next run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from applesync.core.journal import Journal
from applesync.device.base import DeviceSession, RemoteFile

CHUNK = 1024 * 1024  # 1 MiB


class CopyError(Exception):
    """Copy error unrelated to the device (disk full, target taken…)."""


class CopyCancelled(Exception):
    """Clean interruption requested by the user."""


@dataclass
class CopyResult:
    remote: RemoteFile
    local_relpath: str
    sha256: str
    bytes_copied_this_run: int   # bytes actually transferred this time
    resumed_from: int            # 0 for a fresh copy
    duration_s: float


ProgressCb = Callable[[int, int], None]   # (bytes done for this file, total size)


def _sidecar_path(part_path: Path) -> Path:
    return part_path.with_name(part_path.name + ".meta.json")


def _read_sidecar(part_path: Path) -> Optional[dict]:
    sc = _sidecar_path(part_path)
    if not sc.exists():
        return None
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_sidecar(part_path: Path, remote: RemoteFile) -> None:
    sc = _sidecar_path(part_path)
    sc.write_text(
        json.dumps(
            {
                "source_path": remote.path,
                "size": remote.size,
                "mtime": remote.mtime,
            }
        ),
        encoding="utf-8",
    )


def copy_file(
    session: DeviceSession,
    remote: RemoteFile,
    dest_root: Path,
    local_relpath: str,
    journal: Journal,
    cancel: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[ProgressCb] = None,
    chunk_size: int = CHUNK,
) -> CopyResult:
    """Copy `remote` to `dest_root/local_relpath`. See the module guarantees."""
    start = time.time()
    dest_root = Path(dest_root)
    target = dest_root / local_relpath
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        # The planner must never send us here; belt and braces.
        raise CopyError(f"target already taken, copy refused: {target}")

    # --- possible resume ----------------------------------------------------
    resume_offset = 0
    hasher = hashlib.sha256()
    sidecar = _read_sidecar(part)
    if part.exists() and sidecar is not None:
        same_identity = (
            sidecar.get("source_path") == remote.path
            and sidecar.get("size") == remote.size
            and sidecar.get("mtime") == remote.mtime
        )
        part_size = part.stat().st_size
        if same_identity and 0 < part_size <= remote.size:
            # Re-hash the partial file: the final SHA covers the whole file.
            with open(part, "rb") as fh:
                while True:
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    hasher.update(block)
            resume_offset = part_size
            journal.event(
                "file_resumed",
                path=remote.path,
                offset=resume_offset,
                total=remote.size,
            )
        else:
            journal.event(
                "stale_partial_discarded",
                path=remote.path,
                reason="source identity changed" if not same_identity
                       else "inconsistent size",
            )
            part.unlink()
            _sidecar_path(part).unlink(missing_ok=True)
    elif part.exists():
        # Orphan .part without a sidecar: unknown provenance, start over.
        journal.event("partial_without_sidecar_discarded", path=remote.path)
        part.unlink()

    _write_sidecar(part, remote)

    # --- transfer -----------------------------------------------------------
    copied_this_run = 0
    mode = "r+b" if resume_offset else "wb"
    with session.open_file(remote.path) as reader, open(part, mode) as out:
        if resume_offset:
            reader.seek(resume_offset)
            out.seek(resume_offset)
        pos = resume_offset
        while pos < remote.size:
            if cancel is not None and cancel():
                out.flush()
                os.fsync(out.fileno())
                journal.event("copy_interrupted", path=remote.path, offset=pos)
                raise CopyCancelled(remote.path)
            want = min(chunk_size, remote.size - pos)
            data = reader.read(want)
            if not data:
                # End of stream before the announced size: never silent.
                raise CopyError(
                    f"{remote.path}: stream ended at {pos} bytes, "
                    f"{remote.size} expected"
                )
            out.write(data)
            hasher.update(data)
            pos += len(data)
            copied_this_run += len(data)
            if progress_cb is not None:
                progress_cb(pos, remote.size)
        out.flush()
        os.fsync(out.fileno())

    # --- checks and atomic swap ---------------------------------------------
    actual = part.stat().st_size
    if actual != remote.size:
        raise CopyError(
            f"{remote.path}: wrote {actual} bytes, source size is {remote.size}"
        )
    sha = hasher.hexdigest()
    os.utime(part, (time.time(), remote.mtime))  # local mtime = source mtime
    os.replace(part, target)                     # atomic on the same volume
    _sidecar_path(part).unlink(missing_ok=True)

    journal.event(
        "file_copied",
        path=remote.path,
        local=str(local_relpath),
        size=remote.size,
        sha256=sha,
        resumed_from=resume_offset,
        duration_s=round(time.time() - start, 3),
    )
    return CopyResult(
        remote=remote,
        local_relpath=str(local_relpath),
        sha256=sha,
        bytes_copied_this_run=copied_this_run,
        resumed_from=resume_offset,
        duration_s=time.time() - start,
    )
