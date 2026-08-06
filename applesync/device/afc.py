"""Backend réel : iPhone via usbmuxd + lockdown + AFC (pymobiledevice3).

pymobiledevice3 ≥ 10 est entièrement asynchrone ; ce module l'enveloppe dans
une façade synchrone (boucle asyncio dédiée dans un thread) pour respecter le
contrat `DeviceBackend`/`DeviceSession`, synchrone par conception.

Points notables :
- Traduction systématique des exceptions pymobiledevice3 vers la hiérarchie
  `DeviceError` : la logique métier ne voit jamais une exception étrangère.
- `FILE_SEEK` n'est plus exposé par pymobiledevice3 10.x ; on construit le
  paquet nous-mêmes (handle u64, whence u64, offset i64 — format vérifié dans
  libimobiledevice, implémentation C de référence) et on VÉRIFIE la position
  obtenue via `FILE_TELL` avant de reprendre une copie. Une reprise à la
  mauvaise position produirait un fichier corrompu : on préfère échouer.
- Aucune méthode d'écriture/suppression vers l'appareil n'est exposée.
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
# Racines supplémentaires couvertes par la sauvegarde, au-delà du DCIM :
# des photos de la bibliothèque peuvent vivre hors DCIM — originaux gérés par
# les fonctions iCloud (CPLAssets) et éléments d'albums partagés iCloud
# (PhotoCloudSharingData). Le préfixe d'inventaire = le nom du dossier sous
# /PhotoData, ce qui rend la résolution de chemin triviale.
PHOTODATA_ROOTS = ("CPLAssets", "PhotoCloudSharingData")
CPL_PREFIX = "CPLAssets/"                      # compat tests/documentation
SHARED_PREFIX = "PhotoCloudSharingData/"
OP_TIMEOUT_S = 120           # une opération AFC individuelle
CONNECT_TIMEOUT_S = 30
PAIR_TIMEOUT_S = 5           # au-delà : « touchez Se fier » affiché à l'utilisateur
STAT_CONCURRENCY = 8         # stats parallèles pendant l'énumération

_fseek_req = Struct("handle" / Int64ul, "whence" / Int64ul, "offset" / Int64sl)
_ftell_req = Struct("handle" / Int64ul)


# ---------------------------------------------------------------------------
# Traduction des exceptions
# ---------------------------------------------------------------------------

def _translate(e: BaseException) -> DeviceError:
    """Convertit une exception pymobiledevice3/réseau en DeviceError."""
    if isinstance(e, DeviceError):
        return e
    if isinstance(e, (pmd3_exc.PasswordRequiredError, pmd3_exc.PasscodeRequiredError)):
        return DeviceLockedError(
            "iPhone verrouillé — déverrouillez l'écran puis réessayez."
        )
    if isinstance(e, (pmd3_exc.PairingDialogResponsePendingError,)):
        return DeviceUntrustedError(
            "En attente d'appairage — déverrouillez l'iPhone et touchez "
            "« Se fier à cet ordinateur »."
        )
    if isinstance(e, (pmd3_exc.UserDeniedPairingError,)):
        return DeviceUntrustedError(
            "Appairage refusé sur l'iPhone. Débranchez, rebranchez, puis "
            "acceptez « Se fier à cet ordinateur »."
        )
    if isinstance(e, (pmd3_exc.NotPairedError, pmd3_exc.InvalidHostIDError,
                      pmd3_exc.PairingError, pmd3_exc.FatalPairingError,
                      pmd3_exc.NotTrustedError)):
        return DeviceUntrustedError(
            "Appareil non appairé avec ce PC — acceptez « Se fier » sur l'iPhone."
        )
    if isinstance(e, (pmd3_exc.NoDeviceConnectedError, pmd3_exc.DeviceNotFoundError)):
        return DeviceAbsentError("Aucun iPhone détecté sur ce port USB.")
    if isinstance(e, pmd3_exc.ConnectionFailedToUsbmuxdError):
        return UsbmuxdUnavailableError(
            "usbmuxd injoignable sur 127.0.0.1:27015 — installez iTunes "
            "(Apple Mobile Device Support) ou les pilotes CopyTrans, ou "
            "démarrez le service s'il est arrêté."
        )
    if isinstance(e, (pmd3_exc.ConnectionTerminatedError, pmd3_exc.StreamClosedError,
                      pmd3_exc.ConnectionFailedError, pmd3_exc.MuxException,
                      ConnectionError, EOFError, OSError, asyncio.IncompleteReadError)):
        return DeviceDisconnectedError(
            f"Session appareil interrompue ({type(e).__name__}: {e}). "
            f"Écran verrouillé ou câble débranché ?"
        )
    if isinstance(e, pmd3_exc.StartServiceError):
        msg = str(e)
        if "PasswordProtected" in msg or "Password" in msg:
            return DeviceLockedError(
                "iPhone verrouillé — déverrouillez l'écran puis réessayez."
            )
        return DeviceError(f"Service AFC indisponible : {msg}")
    return DeviceError(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Boucle asyncio dédiée (façade synchrone)
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
                f"Opération sans réponse après {timeout:.0f} s — câble débranché "
                f"ou iPhone verrouillé ?"
            ) from None
        except BaseException as e:
            raise _translate(e) from e

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Lecteur de fichier
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
        """FILE_SEEK construit à la main + position vérifiée par FILE_TELL."""
        afc = self._session._afc
        loop = self._session._loop

        async def _seek_and_verify() -> int:
            status, _ = await afc._send_and_wait(
                AfcOpcode.FILE_SEEK,
                _fseek_req.build({"handle": self._handle, "whence": 0, "offset": offset}),
            )
            if status != AfcError.SUCCESS:
                raise FileReadError(self._path, offset, f"seek refusé ({status})")
            status, data = await afc._send_and_wait(
                AfcOpcode.FILE_TELL, _ftell_req.build({"handle": self._handle})
            )
            if status != AfcError.SUCCESS or len(data) < 8:
                raise FileReadError(self._path, offset, f"tell refusé ({status})")
            return int.from_bytes(data[:8], "little")

        pos = loop.call(_seek_and_verify())
        if pos != offset:
            raise FileReadError(
                self._path,
                offset,
                f"position après seek : {pos} ≠ {offset} attendu — reprise refusée",
            )
        self._pos = offset

    def read(self, size: int) -> bytes:
        if self._closed:
            raise FileReadError(self._path, self._pos, "lecteur fermé")
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
            # Erreur AFC localisée (READ_ERROR…) : contexte exact pour le rapport.
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
            pass  # session probablement tombée ; le handle meurt avec elle


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
        # Zones PhotoData : couvertes si elles existent sur l'appareil.
        # Une absence AFC franche = pas de zone ; une déconnexion, elle,
        # remonte comme telle (traduite à la frontière de la façade).
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
                # Liens symboliques et autres : ignorés — inexistants dans un
                # DCIM iOS ; s'ils apparaissent, l'écart sortira au rapport
                # de vérification (absent_du_manifeste n'arrive jamais ici).

    @staticmethod
    def _resolve(path: str) -> str:
        """Chemin AFC complet d'un chemin d'inventaire (DCIM ou zone PhotoData)."""
        if any(path.startswith(name + "/") for name in PHOTODATA_ROOTS):
            return "/PhotoData/" + path
        return posixpath.join(DCIM_ROOT, path)

    @staticmethod
    def _to_remote_file(rel: str, st: dict) -> RemoteFile:
        mtime = st.get("st_mtime")
        birthtime = st.get("st_birthtime")
        # pymobiledevice3 10.x convertit en datetime ; on fige en secondes epoch.
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
            raise FileReadError(path, 0, f"stat impossible : {e}") from e
        return self._to_remote_file(path, st)

    def open_file(self, path: str) -> RemoteFileReader:
        remote = self.stat(path)
        handle = self._loop.call(self._afc.fopen(self._resolve(path), "r"))
        return AfcFileReader(self, path, handle, remote.size)

    # -- jail Media hors DCIM (lecture seule) : base Photos.sqlite -----------

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
    """Accès à l'iPhone réel. Une seule instance par application."""

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

    # -- contrat -------------------------------------------------------------

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
                # DCIM accessible ? (verrouillé → souvent service refusé avant,
                # mais on vérifie : jamais d'inventaire sur un DCIM absent)
                await afc.stat(DCIM_ROOT)
                return lockdown, afc, info
            except BaseException:
                res = lockdown.close()
                if asyncio.iscoroutine(res):
                    await res
                raise

        lockdown, afc, info = loop.call(_connect(), timeout=CONNECT_TIMEOUT_S + PAIR_TIMEOUT_S)
        return AfcDeviceSession(loop, lockdown, afc, info)
