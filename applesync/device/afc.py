"""Real backend: iPhone over usbmuxd + lockdown + AFC (pymobiledevice3).

pymobiledevice3 >= 10 is fully asynchronous; this module wraps it in a
synchronous facade (a dedicated asyncio loop in its own thread) so it can
honour the `DeviceBackend`/`DeviceSession` contract, which is synchronous by
design.

Notable points:
- Every pymobiledevice3 exception is translated into the `DeviceError`
  hierarchy: business logic never sees a foreign exception.
- `FILE_SEEK` is no longer exposed by pymobiledevice3 10.x, so we build the
  packet ourselves (handle u64, whence u64, offset i64 — layout taken from
  libimobiledevice, the reference C implementation) and VERIFY the resulting
  position with `FILE_TELL` before resuming a copy. Resuming at the wrong
  offset would produce a corrupt file, so we would rather fail.
- No write or delete method towards the device is exposed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import posixpath
import threading
from typing import Iterator, Optional

from construct import Int64sl, Int64ul, Struct

from pymobiledevice3 import exceptions as pmd3_exc
from pymobiledevice3 import usbmux
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcError, AfcOpcode, AfcService

from .base import (
    DeviceAbsentError,
    DeviceBackend,
    DeviceDisconnectedError,
    DeviceError,
    DeviceInfo,
    DeviceLockedError,
    DeviceSession,
    DeviceState,
    DeviceUntrustedError,
    FileReadError,
    RemoteFile,
    RemoteFileReader,
    UsbmuxdUnavailableError,
)

DCIM_ROOT = "/DCIM"
# Extra roots covered by the backup, beyond DCIM: some library photos live
# outside DCIM — originals managed by the iCloud features (CPLAssets) and
# items of iCloud shared albums (PhotoCloudSharingData). The inventory prefix
# is the folder name under /PhotoData, which keeps path resolution trivial.
PHOTODATA_ROOTS = ("CPLAssets", "PhotoCloudSharingData")
CPL_PREFIX = "CPLAssets/"
SHARED_PREFIX = "PhotoCloudSharingData/"
OP_TIMEOUT_S = 120           # a single AFC operation
CONNECT_TIMEOUT_S = 30
PAIR_TIMEOUT_S = 5           # beyond that, the UI tells the user to tap "Trust"
STAT_CONCURRENCY = 8         # parallel stats during enumeration

_fseek_req = Struct("handle" / Int64ul, "whence" / Int64ul, "offset" / Int64sl)
_ftell_req = Struct("handle" / Int64ul)


# ---------------------------------------------------------------------------
# Exception translation
# ---------------------------------------------------------------------------

def _translate(e: BaseException) -> DeviceError:
    """Turn a pymobiledevice3 / network exception into a DeviceError."""
    if isinstance(e, DeviceError):
        return e
    if isinstance(e, (pmd3_exc.PasswordRequiredError, pmd3_exc.PasscodeRequiredError)):
        return DeviceLockedError(
            "iPhone locked — unlock the screen and try again."
        )
    if isinstance(e, (pmd3_exc.PairingDialogResponsePendingError,)):
        return DeviceUntrustedError(
            "Waiting for pairing — unlock the iPhone and tap "
            "\"Trust This Computer\"."
        )
    if isinstance(e, (pmd3_exc.UserDeniedPairingError,)):
        return DeviceUntrustedError(
            "Pairing refused on the iPhone. Unplug, plug back in, then accept "
            "\"Trust This Computer\"."
        )
    if isinstance(e, (pmd3_exc.NotPairedError, pmd3_exc.InvalidHostIDError,
                      pmd3_exc.PairingError, pmd3_exc.FatalPairingError,
                      pmd3_exc.NotTrustedError)):
        return DeviceUntrustedError(
            "Device not paired with this PC — tap \"Trust\" on the iPhone."
        )
    if isinstance(e, (pmd3_exc.NoDeviceConnectedError, pmd3_exc.DeviceNotFoundError)):
        return DeviceAbsentError("No iPhone detected on this USB port.")
    if isinstance(e, pmd3_exc.ConnectionFailedToUsbmuxdError):
        return UsbmuxdUnavailableError(
            "usbmuxd unreachable on 127.0.0.1:27015 — install iTunes "
            "(Apple Mobile Device Support), the Apple Devices app or the "
            "CopyTrans drivers, or start the service if it is stopped."
        )
    if isinstance(e, (pmd3_exc.ConnectionTerminatedError, pmd3_exc.StreamClosedError,
                      pmd3_exc.ConnectionFailedError, pmd3_exc.MuxException,
                      ConnectionError, EOFError, OSError, asyncio.IncompleteReadError)):
        return DeviceDisconnectedError(
            f"Device session interrupted ({type(e).__name__}: {e}). "
            f"Screen locked or cable unplugged?"
        )
    if isinstance(e, pmd3_exc.StartServiceError):
        msg = str(e)
        if "PasswordProtected" in msg or "Password" in msg:
            return DeviceLockedError(
                "iPhone locked — unlock the screen and try again."
            )
        return DeviceError(f"AFC service unavailable: {msg}")
    return DeviceError(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Dedicated asyncio loop (synchronous facade)
# ---------------------------------------------------------------------------

class _LoopThread:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="applesync-afc-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coro, timeout: float = OP_TIMEOUT_S):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise DeviceDisconnectedError(
                f"No answer after {timeout:.0f} s — cable unplugged or iPhone "
                f"locked?"
            ) from None
        except BaseException as e:
            raise _translate(e) from e

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# File reader
# ---------------------------------------------------------------------------

class AfcFileReader(RemoteFileReader):
    def __init__(self, session: "AfcDeviceSession", path: str, handle: int, size: int):
        self._session = session
        self._path = path
        self._handle = handle
        self._size = size
        self._pos = 0
        self._closed = False

    def seek(self, offset: int) -> None:
        """Hand-built FILE_SEEK, then position verified with FILE_TELL."""
        afc = self._session._afc
        loop = self._session._loop

        async def _seek_and_verify() -> int:
            status, _ = await afc._send_and_wait(
                AfcOpcode.FILE_SEEK,
                _fseek_req.build({"handle": self._handle, "whence": 0, "offset": offset}),
            )
            if status != AfcError.SUCCESS:
                raise FileReadError(self._path, offset, f"seek refused ({status})")
            status, data = await afc._send_and_wait(
                AfcOpcode.FILE_TELL, _ftell_req.build({"handle": self._handle})
            )
            if status != AfcError.SUCCESS or len(data) < 8:
                raise FileReadError(self._path, offset, f"tell refused ({status})")
            return int.from_bytes(data[:8], "little")

        pos = loop.call(_seek_and_verify())
        if pos != offset:
            raise FileReadError(
                self._path,
                offset,
                f"position after seek: {pos} != {offset} expected — resume refused",
            )
        self._pos = offset

    def read(self, size: int) -> bytes:
        if self._closed:
            raise FileReadError(self._path, self._pos, "reader closed")
        want = min(size, self._size - self._pos)
        if want <= 0:
            return b""
        try:
            data = self._session._loop.call(
                self._session._afc.fread(self._handle, want)
            )
        except DeviceDisconnectedError:
            raise
        except DeviceError as e:
            # Localised AFC error (READ_ERROR…): exact context for the report.
            raise FileReadError(self._path, self._pos, str(e)) from e
        self._pos += len(data)
        return data

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._session._loop.call(
                self._session._afc.fclose(self._handle), timeout=15
            )
        except DeviceError:
            pass  # session probably gone; the handle dies with it


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class AfcDeviceSession(DeviceSession):
    def __init__(self, loop: _LoopThread, lockdown, afc: AfcService, info: DeviceInfo):
        self._loop = loop
        self._lockdown = lockdown
        self._afc = afc
        self._info = info

    def device_info(self) -> DeviceInfo:
        return self._info

    def walk_dcim(self) -> Iterator[RemoteFile]:
        agen = self._walk_async()

        async def _take(n: int):
            out = []
            try:
                for _ in range(n):
                    out.append(await agen.__anext__())
            except StopAsyncIteration:
                return out, True
            return out, False

        while True:
            batch, done = self._loop.call(_take(64), timeout=OP_TIMEOUT_S * 4)
            yield from batch
            if done:
                return

    async def _walk_async(self):
        async for f in self._walk_root(DCIM_ROOT, ""):
            yield f
        # PhotoData zones: covered when they exist on the device. A clean AFC
        # "not found" means the zone is absent; a disconnection, on the other
        # hand, surfaces as such (translated at the facade boundary).
        for name in PHOTODATA_ROOTS:
            root = f"/PhotoData/{name}"
            try:
                st = await self._afc.stat(root)
                present = st.get("st_ifmt") == "S_IFDIR"
            except pmd3_exc.AfcException:
                present = False
            if present:
                async for f in self._walk_root(root, name + "/"):
                    yield f

    async def _walk_root(self, root: str, prefix: str):
        afc = self._afc
        sem = asyncio.Semaphore(STAT_CONCURRENCY)

        async def stat_one(full: str):
            async with sem:
                return full, await afc.stat(full)

        dirs = [root]
        while dirs:
            d = dirs.pop()
            names = await afc.listdir(d)
            results = await asyncio.gather(
                *(stat_one(posixpath.join(d, n)) for n in names if n not in (".", ".."))
            )
            for full, st in results:
                ifmt = st.get("st_ifmt")
                if ifmt == "S_IFDIR":
                    dirs.append(full)
                elif ifmt == "S_IFREG":
                    yield self._to_remote_file(
                        prefix + posixpath.relpath(full, root), st
                    )
                # Symlinks and anything else are skipped — they do not exist in
                # an iOS photo library; should any appear, the verification
                # report would name them rather than hide them.

    @staticmethod
    def _resolve(path: str) -> str:
        """Full AFC path for an inventory path (DCIM or a PhotoData zone)."""
        if any(path.startswith(name + "/") for name in PHOTODATA_ROOTS):
            return "/PhotoData/" + path
        return posixpath.join(DCIM_ROOT, path)

    @staticmethod
    def _to_remote_file(rel: str, st: dict) -> RemoteFile:
        mtime = st.get("st_mtime")
        birthtime = st.get("st_birthtime")
        # pymobiledevice3 10.x returns datetimes; freeze them to epoch seconds.
        mtime_s = int(mtime.timestamp()) if hasattr(mtime, "timestamp") else int(mtime)
        birth_s = (
            int(birthtime.timestamp())
            if hasattr(birthtime, "timestamp")
            else (int(birthtime) if birthtime is not None else None)
        )
        return RemoteFile(
            path=rel, size=int(st["st_size"]), mtime=mtime_s, birthtime=birth_s
        )

    def stat(self, path: str) -> RemoteFile:
        try:
            st = self._loop.call(self._afc.stat(self._resolve(path)))
        except DeviceError:
            raise
        except pmd3_exc.AfcException as e:
            raise FileReadError(path, 0, f"stat failed: {e}") from e
        return self._to_remote_file(path, st)

    def open_file(self, path: str) -> RemoteFileReader:
        remote = self.stat(path)
        handle = self._loop.call(self._afc.fopen(self._resolve(path), "r"))
        return AfcFileReader(self, path, handle, remote.size)

    # -- Media jail outside DCIM (read-only): the Photos database ------------

    def stat_media(self, path: str) -> int:
        try:
            st = self._loop.call(self._afc.stat(path))
        except DeviceDisconnectedError:
            raise
        except DeviceError as e:
            raise FileReadError(path, 0, str(e)) from e
        return int(st["st_size"])

    def open_media(self, path: str) -> RemoteFileReader:
        size = self.stat_media(path)
        handle = self._loop.call(self._afc.fopen(path, "r"))
        return AfcFileReader(self, path, handle, size)

    def close(self) -> None:
        async def _close_all():
            try:
                await self._afc.close()
            finally:
                res = self._lockdown.close()
                if asyncio.iscoroutine(res):
                    await res

        try:
            self._loop.call(_close_all(), timeout=15)
        except DeviceError:
            pass


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class AfcBackend(DeviceBackend):
    """Access to a real iPhone. One instance per application."""

    def __init__(self, usbmux_address: Optional[str] = None):
        self._usbmux_address = usbmux_address
        self._loop: Optional[_LoopThread] = None
        self._lock = threading.Lock()

    @property
    def loop(self) -> _LoopThread:
        with self._lock:
            if self._loop is None:
                self._loop = _LoopThread()
            return self._loop

    def shutdown(self) -> None:
        with self._lock:
            if self._loop is not None:
                self._loop.shutdown()
                self._loop = None

    # -- contract ------------------------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        try:
            devices = self.loop.call(
                usbmux.list_devices(self._usbmux_address), timeout=10
            )
        except DeviceAbsentError:
            return []
        return [
            DeviceInfo(udid=d.serial)
            for d in devices
            if getattr(d, "is_usb", True)
        ]

    def probe_state(self, udid: str) -> DeviceState:
        try:
            devices = self.list_devices()
        except UsbmuxdUnavailableError:
            return DeviceState.NO_USBMUXD
        if not any(d.udid == udid for d in devices):
            return DeviceState.ABSENT

        async def _probe():
            client = await create_using_usbmux(
                serial=udid,
                autopair=False,
                usbmux_address=self._usbmux_address,
            )
            res = client.close()
            if asyncio.iscoroutine(res):
                await res

        try:
            self.loop.call(_probe(), timeout=CONNECT_TIMEOUT_S)
            return DeviceState.READY
        except DeviceLockedError:
            return DeviceState.LOCKED
        except DeviceUntrustedError:
            return DeviceState.UNTRUSTED
        except DeviceError:
            return DeviceState.ERROR

    def connect(self, udid: str) -> DeviceSession:
        loop = self.loop

        async def _connect():
            lockdown = await create_using_usbmux(
                serial=udid,
                autopair=True,
                pair_timeout=PAIR_TIMEOUT_S,
                usbmux_address=self._usbmux_address,
            )
            try:
                values = await lockdown.get_value()
                info = DeviceInfo(
                    udid=udid,
                    name=values.get("DeviceName", ""),
                    model=values.get("ProductType", ""),
                    ios_version=values.get("ProductVersion", ""),
                )
                afc = AfcService(lockdown)
                await afc.connect()
                # Is DCIM reachable? (When locked the service is usually
                # refused earlier, but we check: never inventory a missing
                # DCIM.)
                await afc.stat(DCIM_ROOT)
                return lockdown, afc, info
            except BaseException:
                res = lockdown.close()
                if asyncio.iscoroutine(res):
                    await res
                raise

        lockdown, afc, info = loop.call(_connect(), timeout=CONNECT_TIMEOUT_S + PAIR_TIMEOUT_S)
        return AfcDeviceSession(loop, lockdown, afc, info)
