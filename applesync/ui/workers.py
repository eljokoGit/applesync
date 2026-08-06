"""Worker threads: every device/disk access happens off the UI thread.

Each worker is a QThread; communication goes exclusively through Qt signals
(queued connections, therefore thread-safe). Workers receive a
`threading.Event` for cancellation, which the UI sets to stop cleanly.
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from applesync.core.engine import PreparedRun, SyncEngine
from applesync.core.manifest import Manifest
from applesync.core.stability import run_stability_check
from applesync.core.verifier import verify_against_inventory
from applesync.device.base import DeviceBackend, DeviceState, UsbmuxdUnavailableError


class UpdateCheckWorker(QThread):
    """Ask GitHub once at start-up. Silent when there is nothing to report."""

    update_available = Signal(object)      # UpdateInfo

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self) -> None:
        from applesync.core.updates import check_for_update

        info = check_for_update(self.current_version)
        if info is not None:
            self.update_available.emit(info)


class DeviceWatcher(QThread):
    """Watch the USB bus and publish the actionable device state."""

    state_changed = Signal(object, str)   # (DeviceState | None, udid)

    POLL_S = 2.5

    def __init__(self, backend: DeviceBackend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._stop = threading.Event()
        self.paused = False               # True while a sync is running

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        last: tuple = (None, "")
        while not self._stop.is_set():
            try:
                if self.paused:
                    time.sleep(0.5)
                    continue
                devices = self.backend.list_devices()
                if not devices:
                    current = (DeviceState.ABSENT, "")
                else:
                    udid = devices[0].udid
                    current = (self.backend.probe_state(udid), udid)
            except UsbmuxdUnavailableError:
                current = (DeviceState.NO_USBMUXD, "")
            except Exception:
                current = (DeviceState.ERROR, "")
            if current != last:
                last = current
                self.state_changed.emit(current[0], current[1])
            self._stop.wait(self.POLL_S)


class PrepareWorker(QThread):
    """Phase 1: inventory (double enumeration) + plan. Writes nothing."""

    phase = Signal(str)
    inventory_progress = Signal(int, str)
    done = Signal(object)                # PreparedRun
    failed = Signal(str, str)            # (message, traceback)

    def __init__(self, engine: SyncEngine, udid: str, cancel: threading.Event,
                 parent=None):
        super().__init__(parent)
        self.engine = engine
        self.udid = udid
        self.cancel = cancel

    def run(self) -> None:
        try:
            prepared = self.engine.prepare(
                self.udid,
                phase_cb=self.phase.emit,
                inventory_progress=lambda n, ph: self.inventory_progress.emit(n, ph),
                cancel=self.cancel.is_set,
            )
            self.done.emit(prepared)
        except Exception as e:
            self.failed.emit(str(e), traceback.format_exc())


class ExecuteWorker(QThread):
    """Phase 2: copy + verification + report, on a validated plan."""

    phase = Signal(str)
    progress = Signal(object)            # ProgressSnapshot
    verify_progress = Signal(int, int, str)   # (done, total, file)
    done = Signal(object)                # RunReport
    failed = Signal(str, str)

    def __init__(
        self,
        engine: SyncEngine,
        prepared: PreparedRun,
        cancel: threading.Event,
        deep_verify: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.prepared = prepared
        self.cancel = cancel
        self.deep_verify = deep_verify

    def run(self) -> None:
        try:
            report = self.engine.execute(
                self.prepared,
                progress=self.progress.emit,
                phase_cb=self.phase.emit,
                cancel=self.cancel.is_set,
                deep_verify=self.deep_verify,
                verify_progress=lambda i, n, p: self.verify_progress.emit(i, n, p),
            )
            self.done.emit(report)
        except Exception as e:
            self.failed.emit(str(e), traceback.format_exc())


class VerifyWorker(QThread):
    """Standalone verification of the WHOLE destination against a fresh
    inventory."""

    phase = Signal(str)
    progress = Signal(int, int, str)
    done = Signal(object)                # VerificationReport
    failed = Signal(str, str)

    def __init__(self, backend: DeviceBackend, udid: str, dest_root: Path,
                 cancel: threading.Event, deep: bool = True, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.udid = udid
        self.dest_root = Path(dest_root)
        self.cancel = cancel
        self.deep = deep

    def run(self) -> None:
        try:
            from applesync.core.inventory import take_inventory

            self.phase.emit("Fresh device inventory (double enumeration)…")
            session = self.backend.connect(self.udid)
            try:
                inventory = take_inventory(session, cancel=self.cancel.is_set)
            finally:
                session.close()
            self.phase.emit("Re-reading the destination…")
            with Manifest(self.dest_root) as manifest:
                report = verify_against_inventory(
                    inventory.files,
                    manifest,
                    self.dest_root,
                    deep_hash=self.deep,
                    progress_cb=lambda i, n, p: self.progress.emit(i, n, p),
                    cancel=self.cancel.is_set,
                )
            self.done.emit(report)
        except Exception as e:
            self.failed.emit(str(e), traceback.format_exc())


class AlbumsWorker(QThread):
    """Album recovery: copy Photos.sqlite, parse it, materialise the albums.

    Entirely separate from the backup: its failure affects nothing."""

    phase = Signal(str)
    progress = Signal(int, int)          # (bytes copied, total) of the database
    mat_progress = Signal(int, int)      # (album files copied, total)
    done = Signal(object, str)           # (AlbumsReport, markdown report path)
    failed = Signal(str, str)

    def __init__(self, backend: DeviceBackend, udid: str, dest_root: Path,
                 cancel: threading.Event, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.udid = udid
        self.dest_root = Path(dest_root)
        self.cancel = cancel

    def run(self) -> None:
        try:
            from applesync.core.albums import (
                fetch_photos_db,
                materialize_albums,
                parse_albums,
                save_report,
            )

            self.phase.emit("Connecting to the device…")
            session = self.backend.connect(self.udid)
            try:
                self.phase.emit("Copying the Photos database…")
                db = fetch_photos_db(
                    session,
                    self.dest_root / ".applesync" / "photodata",
                    progress_cb=lambda a, b: self.progress.emit(a, b),
                    cancel=self.cancel.is_set,
                    phase_cb=self.phase.emit,
                )
            finally:
                session.close()
            self.phase.emit("Parsing the albums (schema verified)…")
            data = parse_albums(db)
            self.phase.emit("Building the album folders (copies)…")
            with Manifest(self.dest_root) as m:
                report = materialize_albums(
                    data, m, self.dest_root,
                    progress_cb=lambda i, n: self.mat_progress.emit(i, n),
                )
            path = save_report(report, self.dest_root)
            self.done.emit(report, str(path))
        except Exception as e:
            self.failed.emit(str(e), traceback.format_exc())


class StabilityWorker(QThread):
    """Success criterion: 3 identical inventories, unplugged in between."""

    phase = Signal(str)
    instruction = Signal(str)            # instruction to the user (unplug…)
    inventory_progress = Signal(int, str)
    done = Signal(object)                # StabilityResult
    failed = Signal(str, str)

    def __init__(self, backend: DeviceBackend, udid: str, cancel: threading.Event,
                 simulate: bool = False, rounds: int = 3, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.udid = udid
        self.cancel = cancel
        self.simulate = simulate
        self.rounds = rounds

    def _wait_replug(self, next_round: int) -> None:
        if self.simulate:
            self.instruction.emit(
                f"(Simulation) Unplug/replug simulated before pass {next_round}."
            )
            time.sleep(1.0)
            return
        self.instruction.emit(
            f"Pass {next_round - 1} done. UNPLUG the device now."
        )
        while not self.cancel.is_set():
            if not any(d.udid == self.udid for d in self._safe_list()):
                break
            time.sleep(1.0)
        self.instruction.emit("Plug the device back in and unlock it…")
        while not self.cancel.is_set():
            if any(d.udid == self.udid for d in self._safe_list()):
                if self.backend.probe_state(self.udid) == DeviceState.READY:
                    break
            time.sleep(1.0)
        if self.cancel.is_set():
            raise InterruptedError("stability check interrupted")
        self.instruction.emit(f"Device ready — pass {next_round} running…")

    def _safe_list(self):
        """Tolerant list_devices: if usbmuxd drops while waiting for the
        replug, treat the device as absent and keep waiting."""
        try:
            return self.backend.list_devices()
        except UsbmuxdUnavailableError:
            return []

    def run(self) -> None:
        try:
            result = run_stability_check(
                self.backend,
                self.udid,
                rounds=self.rounds,
                wait_between_rounds=self._wait_replug,
                progress_cb=lambda n, ph: self.inventory_progress.emit(n, ph),
                cancel=self.cancel.is_set,
            )
            self.done.emit(result)
        except Exception as e:
            self.failed.emit(str(e), traceback.format_exc())
