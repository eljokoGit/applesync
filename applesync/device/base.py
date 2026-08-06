"""Abstract device-access contract.

All business logic (inventory, plan, copy, verification) sees only these
interfaces. Absolute rule: no write or delete method exists in this contract —
the iPhone is read-only by construction, not by policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class DeviceState(Enum):
    """Device state as presented to the UI."""

    ABSENT = "absent"            # usbmuxd answers but no device on the bus
    NO_USBMUXD = "no_usbmuxd"    # usbmuxd / Apple Mobile Device unreachable
    LOCKED = "locked"            # detected but locked with a passcode
    UNTRUSTED = "untrusted"      # detected but the user has not tapped "Trust"
    READY = "ready"              # paired, a session can be opened
    ERROR = "error"              # detected but unreachable (unknown cause)


@dataclass(frozen=True)
class DeviceInfo:
    udid: str
    name: str = ""
    model: str = ""
    ios_version: str = ""


@dataclass(frozen=True)
class RemoteFile:
    """A file on the device, as seen by the inventory.

    `path` is relative to the DCIM root, POSIX separators
    (e.g. "100APPLE/IMG_0001.HEIC"). Files living outside DCIM carry their
    zone as a prefix (e.g. "CPLAssets/group159/…").
    """

    path: str
    size: int
    mtime: int                    # epoch seconds (deliberately truncated)
    birthtime: Optional[int] = None

    @property
    def identity(self) -> tuple[str, int, int]:
        """Incremental identity: path + size + mtime.

        Stronger than the name alone: a file recreated under the same name
        with different content changes size and/or mtime.
        """
        return (self.path, self.size, self.mtime)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Error hierarchy. Every implementation MUST translate its native errors into
# these classes: business logic knows nothing else.
# ---------------------------------------------------------------------------

class DeviceError(Exception):
    """Device-access error (base class)."""


class DeviceAbsentError(DeviceError):
    """No device connected."""


class UsbmuxdUnavailableError(DeviceError):
    """usbmuxd (Apple Mobile Device Support / CopyTrans) is not answering.

    Distinct from DeviceAbsentError: here the PC is not ready, rather than
    the iPhone being missing."""


class DeviceLockedError(DeviceError):
    """Device locked with a passcode: unlock the screen."""


class DeviceUntrustedError(DeviceError):
    """Pairing refused or pending: tap "Trust" on the iPhone."""


class DeviceDisconnectedError(DeviceError):
    """The session dropped mid-operation (unplugged, screen locked…)."""


class FileReadError(DeviceError):
    """A file read failed, with the exact position."""

    def __init__(self, path: str, offset: int, message: str = ""):
        self.path = path
        self.offset = offset
        super().__init__(f"Read failed on {path} at byte {offset}: {message}")


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

class RemoteFileReader(ABC):
    """Sequential reader over a device file, with positioning."""

    @abstractmethod
    def seek(self, offset: int) -> None:
        """Move to `offset` bytes from the start."""

    @abstractmethod
    def read(self, size: int) -> bytes:
        """Read up to `size` bytes. b'' means end of file.

        Raises FileReadError or DeviceDisconnectedError on trouble — never a
        silent short read before the end of the file.
        """

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "RemoteFileReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DeviceSession(ABC):
    """An open session with a paired device.

    A session can drop at any moment (screen lock, unplug): every method may
    raise DeviceDisconnectedError.
    """

    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def walk_dcim(self) -> Iterator[RemoteFile]:
        """Recursively enumerate EVERY file of the photo library.

        Order is not guaranteed. Any error interrupts iteration with an
        exception: by contract, a generator that finishes without raising has
        enumerated everything. (The defence against a silent truncation
        despite that contract is the double enumeration, see core/inventory.)
        """

    @abstractmethod
    def stat(self, path: str) -> RemoteFile:
        """Stat a file by inventory path. Raises if missing."""

    @abstractmethod
    def open_file(self, path: str) -> RemoteFileReader: ...

    # -- Media access outside DCIM (read-only), for the Photos database ------
    # Absolute paths inside the AFC jail (/var/mobile/Media), e.g. "/PhotoData/…".
    # Optional to implement: absence raises, never returns empty.

    def stat_media(self, path: str) -> int:
        """Size of a file in the Media jail. Raises if missing/unreachable."""
        raise FileReadError(path, 0, "Media access unavailable on this backend")

    def open_media(self, path: str) -> RemoteFileReader:
        """Open a file of the Media jail for reading."""
        raise FileReadError(path, 0, "Media access unavailable on this backend")

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "DeviceSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DeviceBackend(ABC):
    """Entry point: device discovery and session opening."""

    @abstractmethod
    def list_devices(self) -> list[DeviceInfo]:
        """Devices currently visible on the bus (without opening a session)."""

    @abstractmethod
    def probe_state(self, udid: str) -> DeviceState:
        """Actionable device state (locked / untrusted / ready)."""

    @abstractmethod
    def connect(self, udid: str) -> DeviceSession:
        """Open a session. Raises DeviceLockedError / DeviceUntrustedError /
        DeviceAbsentError depending on the actual state."""
