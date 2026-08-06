"""Simulated backend: realistic, deterministic library with fault injection.

Each file's content is derived from sha256(seed + path) and generated as a
stream, so a 100 GB library can be simulated without storing anything. Same
seed, same tree, same bytes, same hashes. All business logic is validated
against this.

Injectable faults (the ones that actually happen in the field):
- device locked / untrusted at connection time;
- disconnection during enumeration;
- enumeration truncated WITHOUT any error (the observed MTP defect);
- read failing mid-file;
- disconnection mid-read.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from .base import (
    DeviceAbsentError,
    DeviceBackend,
    DeviceDisconnectedError,
    DeviceInfo,
    DeviceLockedError,
    DeviceSession,
    DeviceState,
    DeviceUntrustedError,
    FileReadError,
    RemoteFile,
    RemoteFileReader,
)

_CHUNK = 65536


# ---------------------------------------------------------------------------
# Profiles: shape of the simulated library
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimProfile:
    """Generation parameters for the simulated file tree."""

    seed: int = 20260804
    first_month: tuple[int, int] = (2017, 1)     # (year, month)
    last_month: tuple[int, int] = (2026, 8)
    files_per_folder_range: tuple[int, int] = (250, 450)
    heic_size_range: tuple[int, int] = (1_200_000, 3_200_000)
    mov_size_range: tuple[int, int] = (2_000_000, 15_000_000)
    mov_ratio: float = 0.10                       # share of videos

    @staticmethod
    def realistic() -> "SimProfile":
        """~40,000 files, ~100 GB — metadata only in practice."""
        return SimProfile()

    @staticmethod
    def small(seed: int = 42) -> "SimProfile":
        """Small profile for tests: ~180 files of a few KB."""
        return SimProfile(
            seed=seed,
            first_month=(2023, 1),
            last_month=(2023, 12),
            files_per_folder_range=(12, 18),
            heic_size_range=(2_000, 20_000),
            mov_size_range=(30_000, 120_000),
        )

    @staticmethod
    def demo(seed: int = 20240101) -> "SimProfile":
        """UI demo profile: ~300 files, ~300 MB — large enough to watch
        progress, throughput and ETA, small enough for a quick trial."""
        return SimProfile(
            seed=seed,
            first_month=(2024, 1),
            last_month=(2025, 6),
            files_per_folder_range=(12, 20),
            heic_size_range=(300_000, 1_200_000),
            mov_size_range=(2_000_000, 8_000_000),
        )


def _months(profile: SimProfile) -> List[str]:
    (y, m), (ly, lm) = profile.first_month, profile.last_month
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}{m:02d}_a")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def build_tree(profile: SimProfile) -> List[RemoteFile]:
    """Build the whole simulated tree, sorted by path. Deterministic."""
    rng = random.Random(profile.seed)
    files: List[RemoteFile] = []
    counter = 1
    for folder in _months(profile):
        year, month = int(folder[:4]), int(folder[4:6])
        month_epoch = int(
            (year - 1970) * 365.25 * 86400 + (month - 1) * 30.4 * 86400
        )
        n = rng.randint(*profile.files_per_folder_range)
        for _ in range(n):
            is_mov = rng.random() < profile.mov_ratio
            ext = "MOV" if is_mov else "HEIC"
            size = rng.randint(
                *(profile.mov_size_range if is_mov else profile.heic_size_range)
            )
            name = f"IMG_{counter:05d}.{ext}"
            mtime = month_epoch + rng.randint(0, 29 * 86400)
            files.append(
                RemoteFile(
                    path=f"{folder}/{name}",
                    size=size,
                    mtime=mtime,
                    birthtime=mtime - rng.randint(0, 3600),
                )
            )
            counter += 1
    return files


def content_stream(seed: int, path: str, size: int, offset: int = 0) -> Iterator[bytes]:
    """Deterministic bytes of a simulated file, starting at `offset`.

    The stream is a function of (seed, path) only: reproducible and
    addressable at any offset, which is what makes resume testable.
    """
    key = hashlib.sha256(f"{seed}:{path}".encode()).digest()
    block_index = offset // 32
    skip = offset % 32
    produced = offset
    while produced < size:
        block = hashlib.sha256(key + block_index.to_bytes(8, "big")).digest()
        if skip:
            block = block[skip:]
            skip = 0
        take = min(len(block), size - produced)
        yield block[:take]
        produced += take
        block_index += 1


def content_sha256(seed: int, path: str, size: int) -> str:
    """Expected SHA-256 of a simulated file (reference for tests)."""
    h = hashlib.sha256()
    for chunk in content_stream(seed, path, size):
        h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Fault plan
# ---------------------------------------------------------------------------

@dataclass
class FaultPlan:
    """Faults to inject. Every field is optional (default: no fault).

    The `walk_calls` counter allows targeting the Nth operation, for instance
    truncating only the second enumeration.
    """

    locked: bool = False                      # connect() -> DeviceLockedError
    untrusted: bool = False                   # connect() -> DeviceUntrustedError
    absent: bool = False                      # connect() -> DeviceAbsentError

    # Disconnect after N enumerated entries (on enumeration number
    # `disconnect_on_walk_index`, 1-based; 0 means every enumeration).
    disconnect_after_entries: Optional[int] = None
    disconnect_on_walk_index: int = 0

    # SILENT truncation: enumeration number `truncate_on_walk_index` (1-based)
    # omits `truncate_drop_count` files without raising. The MTP defect.
    truncate_on_walk_index: Optional[int] = None
    truncate_drop_count: int = 50

    # Read: failure at byte N of file `fail_read_path`
    fail_read_path: Optional[str] = None
    fail_read_at_byte: int = 0
    fail_read_as_disconnect: bool = False     # True -> the session drops

    # Internal state (call counters)
    walk_calls: int = field(default=0, init=False)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

class _SimReader(RemoteFileReader):
    def __init__(self, session: "SimulatedSession", f: RemoteFile):
        self._session = session
        self._file = f
        self._pos = 0
        self._closed = False

    def seek(self, offset: int) -> None:
        self._session._check_alive()
        if offset < 0 or offset > self._file.size:
            raise FileReadError(self._file.path, offset, "seek out of bounds")
        self._pos = offset

    def read(self, size: int) -> bytes:
        self._session._check_alive()
        if self._closed:
            raise FileReadError(self._file.path, self._pos, "reader closed")
        faults = self._session._faults
        end = min(self._pos + size, self._file.size)

        # Read fault targeting this file: deliver bytes up to the failure
        # point, then fail on the next read.
        if faults.fail_read_path == self._file.path:
            fail_at = faults.fail_read_at_byte
            if self._pos >= fail_at:
                if faults.fail_read_as_disconnect:
                    self._session._drop()
                    raise DeviceDisconnectedError(
                        f"session dropped while reading {self._file.path}"
                    )
                raise FileReadError(self._file.path, self._pos, "injected fault")
            if fail_at < end:
                end = fail_at

        if self._pos >= self._file.size:
            return b""
        backend = self._session._backend
        key = backend.content_key(self._file.path)
        if key in backend.content_bytes:
            data = backend.content_bytes[key][self._pos:end]
        else:
            data = b"".join(
                content_stream(self._session._profile.seed, key, end, self._pos)
            )
        self._pos = end
        return data

    def close(self) -> None:
        self._closed = True


class _BytesReader(RemoteFileReader):
    """Reader over in-memory bytes (simulated Media files)."""

    def __init__(self, path: str, data: Optional[bytes]):
        if data is None:
            raise FileReadError(path, 0, "Media file missing (simulated)")
        self._path = path
        self._data = data
        self._pos = 0

    def seek(self, offset: int) -> None:
        if offset < 0 or offset > len(self._data):
            raise FileReadError(self._path, offset, "seek out of bounds")
        self._pos = offset

    def read(self, size: int) -> bytes:
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class SimulatedSession(DeviceSession):
    def __init__(self, backend: "SimulatedBackend"):
        self._backend = backend
        self._profile = backend.profile
        self._faults = backend.faults
        self._alive = True

    def _check_alive(self) -> None:
        if not self._alive:
            raise DeviceDisconnectedError("session already dropped")

    def _drop(self) -> None:
        self._alive = False

    def device_info(self) -> DeviceInfo:
        self._check_alive()
        return self._backend.INFO

    def walk_dcim(self) -> Iterator[RemoteFile]:
        self._check_alive()
        self._faults.walk_calls += 1
        walk_no = self._faults.walk_calls
        tree = self._backend.tree

        drop: set[int] = set()
        if self._faults.truncate_on_walk_index == walk_no:
            # Deterministic truncation: drop files spread across the tree.
            rng = random.Random(self._profile.seed ^ walk_no)
            k = min(self._faults.truncate_drop_count, len(tree))
            drop = set(rng.sample(range(len(tree)), k))

        disconnect_at = None
        if self._faults.disconnect_after_entries is not None and (
            self._faults.disconnect_on_walk_index in (0, walk_no)
        ):
            disconnect_at = self._faults.disconnect_after_entries

        yielded = 0
        for i, f in enumerate(tree):
            self._check_alive()
            if disconnect_at is not None and yielded >= disconnect_at:
                self._drop()
                raise DeviceDisconnectedError(
                    f"device disconnected after {yielded} entries"
                )
            if i in drop:
                continue  # silent: this is the simulated MTP defect
            yielded += 1
            yield f

    def stat(self, path: str) -> RemoteFile:
        self._check_alive()
        f = self._backend.by_path.get(path)
        if f is None:
            raise FileReadError(path, 0, "file not found")
        return f

    def open_file(self, path: str) -> RemoteFileReader:
        self._check_alive()
        return _SimReader(self, self.stat(path))

    # -- simulated Media jail (album tests) ----------------------------------

    def stat_media(self, path: str) -> int:
        self._check_alive()
        data = self._backend.media_files.get(path)
        if data is None:
            raise FileReadError(path, 0, "Media file missing (simulated)")
        return len(data)

    def open_media(self, path: str) -> RemoteFileReader:
        self._check_alive()
        return _BytesReader(path, self._backend.media_files.get(path))

    def close(self) -> None:
        self._alive = False


class SimulatedBackend(DeviceBackend):
    INFO = DeviceInfo(
        udid="SIMULATOR-0000",
        name="Simulated iPhone",
        model="iPhone-Sim",
        ios_version="17.0-sim",
    )

    def __init__(self, profile: SimProfile | None = None, faults: FaultPlan | None = None):
        self.profile = profile or SimProfile.small()
        self.faults = faults or FaultPlan()
        self.tree: List[RemoteFile] = build_tree(self.profile)
        self.by_path: Dict[str, RemoteFile] = {f.path: f for f in self.tree}
        # Content aliases: path -> path whose bytes it shares (used to
        # simulate genuine content duplicates).
        self.content_alias: Dict[str, str] = {}
        # Explicit contents: path -> real bytes (for tests that need actual
        # formats, e.g. a JPEG carrying EXIF).
        self.content_bytes: Dict[str, bytes] = {}
        # Simulated Media jail outside DCIM (e.g. /PhotoData/Photos.sqlite for
        # the album-recovery tests).
        self.media_files: Dict[str, bytes] = {}

    def content_key(self, path: str) -> str:
        return self.content_alias.get(path, path)

    def add_file_with_content(self, path: str, data: bytes, mtime: int) -> RemoteFile:
        """Add a file whose bytes are provided verbatim."""
        f = self.add_file(path, len(data), mtime)
        self.content_bytes[path] = data
        return f

    # -- tree mutations, for incremental tests -------------------------------

    def add_file(self, path: str, size: int, mtime: int) -> RemoteFile:
        f = RemoteFile(path=path, size=size, mtime=mtime, birthtime=mtime)
        self.tree.append(f)
        self.tree.sort(key=lambda x: x.path)
        self.by_path[path] = f
        return f

    def remove_file(self, path: str) -> None:
        """Simulate a deletion performed BY THE USER on their iPhone.

        (The application itself has no way to write to the device: the
        DeviceSession contract is read-only.)
        """
        self.tree = [f for f in self.tree if f.path != path]
        del self.by_path[path]

    def replace_file(self, path: str, new_size: int, new_mtime: int) -> RemoteFile:
        self.remove_file(path)
        return self.add_file(path, new_size, new_mtime)

    def clone_file(self, src_path: str, new_path: str, mtime: int) -> RemoteFile:
        """Add a file whose CONTENT is identical to `src_path` (true duplicate)."""
        src = self.by_path[src_path]
        clone = self.add_file(new_path, src.size, mtime)
        self.content_alias[new_path] = self.content_key(src_path)
        return clone

    # -- DeviceBackend contract ----------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        if self.faults.absent:
            return []
        return [self.INFO]

    def probe_state(self, udid: str) -> DeviceState:
        if self.faults.absent:
            return DeviceState.ABSENT
        if self.faults.locked:
            return DeviceState.LOCKED
        if self.faults.untrusted:
            return DeviceState.UNTRUSTED
        return DeviceState.READY

    def connect(self, udid: str) -> DeviceSession:
        if self.faults.absent:
            raise DeviceAbsentError("no device")
        if self.faults.locked:
            raise DeviceLockedError("device locked — unlock the screen")
        if self.faults.untrusted:
            raise DeviceUntrustedError("tap \"Trust\" on the iPhone")
        return SimulatedSession(self)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.tree)
