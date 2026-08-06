"""AppleSync main window. Legibility of state before aesthetics.

Window state machine:
    IDLE -> PREPARING -> PLAN_READY -> SYNCING -> IDLE
                                    \\ clean interruption /
    (VERIFYING, STABILITY and ALBUMS are exclusive operations from IDLE)
"""

from __future__ import annotations

import os
import threading
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from applesync import __version__
from applesync.core.config import Config
from applesync.core.engine import COMPLETED, FAILED, INTERRUPTED, PreparedRun, \
    ProgressSnapshot, SyncEngine
from applesync.core.layout import label_for, make_layout
from applesync.core.manifest import Manifest
from applesync.core.report import RunReport, fmt_bytes, fmt_duration
from applesync.core.stability import StabilityResult
from applesync.core.verifier import VerificationReport
from applesync.device.base import DeviceBackend, DeviceState
from applesync.ui.workers import (
    DeviceWatcher,
    ExecuteWorker,
    PrepareWorker,
    StabilityWorker,
    UpdateCheckWorker,
    VerifyWorker,
)

STATE_STYLES = {
    DeviceState.ABSENT: ("#9e9e9e", "No device detected",
                         "The Apple service is answering but sees no device. "
                         "Plug the iPhone in over USB; if the cable is already "
                         "connected, try another cable (many are charge-only) "
                         "or another USB port, and unlock the device."),
    DeviceState.NO_USBMUXD: ("#c62828", "Apple Mobile Device service unreachable",
                             "The usbmuxd driver is not answering on "
                             "127.0.0.1:27015: this PC cannot see any iPhone, "
                             "even plugged in. Install iTunes (Apple Mobile "
                             "Device Support), the Apple Devices app or the "
                             "CopyTrans drivers, then restart. If it is already "
                             "installed, start the \"Apple Mobile Device "
                             "Service\" (services.msc)."),
    DeviceState.LOCKED: ("#e69100", "Device locked",
                         "Unlock the device screen (passcode or Face ID)."),
    DeviceState.UNTRUSTED: ("#e69100", "Device not paired",
                            "On the device: tap \"Trust This Computer\", then "
                            "enter the passcode."),
    DeviceState.READY: ("#1a7f37", "Device ready",
                        "You can run the inventory."),
    DeviceState.ERROR: ("#c62828", "Device detected but unreachable",
                        "Unplug and plug the cable back in. If it persists, "
                        "restart the Apple Mobile Device service."),
}


class UiState(Enum):
    IDLE = auto()
    PREPARING = auto()
    PLAN_READY = auto()
    SYNCING = auto()
    VERIFYING = auto()
    STABILITY = auto()
    ALBUMS = auto()


class MainWindow(QMainWindow):
    def __init__(self, backend: DeviceBackend, simulate: bool = False,
                 config: Config | None = None):
        super().__init__()
        self.backend = backend
        self.simulate = simulate
        self.config = config or Config()
        self.cancel = threading.Event()
        self.prepared: PreparedRun | None = None
        self._expected_files: int | None = None   # count of the last known inventory
        self.current_udid: str = ""
        self.device_state: DeviceState = DeviceState.ABSENT
        self.ui_state = UiState.IDLE
        self._workers: list = []

        title = "AppleSync — iPhone to PC backup"
        if simulate:
            title += "   [SIMULATION MODE — no real device]"
        self.setWindowTitle(title)
        self.resize(920, 780)
        self._build()

        self.watcher = DeviceWatcher(backend)
        self.watcher.state_changed.connect(self._on_device_state)
        self.watcher.start()
        self._refresh_layout_lock()
        self._refresh_buttons()
        self._show_last_run_summary()
        self._start_update_check()

    # ------------------------------------------------------------------ updates
    def _start_update_check(self) -> None:
        """Check once whether a newer version exists.

        Can be disabled with "check_updates": false in the configuration.
        No data is sent, nothing is downloaded or installed."""
        if not self.config.get("check_updates", True):
            return
        w = UpdateCheckWorker(__version__, self)
        w.update_available.connect(self._on_update_available)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_update_available(self, info) -> None:
        self.update_label.setText(
            f"Version {info.latest} is available (you are running "
            f"{info.current}). Updating is manual: see the release notes."
        )
        try:
            self.btn_update.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.btn_update.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(info.url))
        )
        self.update_bar.show()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setSpacing(10)

        # Device state banner
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.StyledPanel)
        banner_lay = QVBoxLayout(self.banner)
        self.state_label = QLabel("Looking for a device…")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self.state_label.setFont(f)
        self.state_hint = QLabel("")
        self.state_hint.setWordWrap(True)
        banner_lay.addWidget(self.state_label)
        banner_lay.addWidget(self.state_hint)
        root.addWidget(self.banner)

        # Destination + layout
        dest_box = QGroupBox("Backup destination")
        dest_v = QVBoxLayout(dest_box)
        dest_row = QHBoxLayout()
        self.dest_label = QLabel(self._dest_text())
        self.dest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dest_btn = QPushButton("Choose…")
        self.dest_btn.clicked.connect(self._choose_dest)
        dest_row.addWidget(self.dest_label, stretch=1)
        dest_row.addWidget(self.dest_btn)
        dest_v.addLayout(dest_row)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Mirror of the device tree (default)", "mirror")
        self.layout_combo.addItem("By date (YYYY/YYYY-MM)", "date")
        self.layout_combo.addItem(
            "Archive (YYYY/YYYY-MM, date renaming, _LivePhotos)", "archive"
        )
        self.screenshots_check = QCheckBox("Screenshots (PNG) apart")
        self.layout_lock_label = QLabel("")
        kind = self.config.get("layout", "mirror")
        idx = self.layout_combo.findData(kind)
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.screenshots_check.setChecked(
            bool(self.config.get("screenshots_apart", False))
        )
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self.screenshots_check.toggled.connect(self._on_layout_changed)
        layout_row.addWidget(self.layout_combo)
        layout_row.addWidget(self.screenshots_check)
        layout_row.addWidget(self.layout_lock_label, stretch=1)
        dest_v.addLayout(layout_row)
        root.addWidget(dest_box)

        # Actions
        actions = QHBoxLayout()
        self.btn_inventory = QPushButton("1. Inventory")
        self.btn_sync = QPushButton("2. Synchronise")
        self.btn_verify = QPushButton("Verify destination")
        self.btn_stability = QPushButton("Stability check (3x)")
        self.btn_duplicates = QPushButton("Duplicates")
        self.btn_albums = QPushButton("Albums")
        self.btn_cancel = QPushButton("Stop cleanly")
        self.btn_inventory.clicked.connect(self._start_prepare)
        self.btn_sync.clicked.connect(self._start_execute)
        self.btn_verify.clicked.connect(self._start_verify)
        self.btn_stability.clicked.connect(self._start_stability)
        self.btn_duplicates.clicked.connect(self._show_duplicates)
        self.btn_albums.clicked.connect(self._start_albums)
        self.btn_cancel.clicked.connect(self._request_cancel)
        for b in (self.btn_inventory, self.btn_sync, self.btn_verify,
                  self.btn_stability, self.btn_duplicates, self.btn_albums,
                  self.btn_cancel):
            actions.addWidget(b)
        root.addLayout(actions)

        # Inventory / plan
        self.plan_box = QGroupBox("Inventory and plan (validate before copying)")
        plan_lay = QVBoxLayout(self.plan_box)
        self.plan_label = QLabel("No inventory yet.")
        self.plan_label.setWordWrap(True)
        self.plan_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        plan_lay.addWidget(self.plan_label)
        root.addWidget(self.plan_box)

        # Progress
        prog_box = QGroupBox("Progress")
        grid = QGridLayout(prog_box)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.lbl_file = QLabel("—")
        self.lbl_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_counts = QLabel("—")
        self.lbl_speed = QLabel("—")
        self.lbl_phase = QLabel("Waiting.")
        grid.addWidget(self.progress_bar, 0, 0, 1, 2)
        grid.addWidget(QLabel("File:"), 1, 0)
        grid.addWidget(self.lbl_file, 1, 1)
        grid.addWidget(QLabel("Progress:"), 2, 0)
        grid.addWidget(self.lbl_counts, 2, 1)
        grid.addWidget(QLabel("Throughput / ETA:"), 3, 0)
        grid.addWidget(self.lbl_speed, 3, 1)
        grid.addWidget(QLabel("Phase:"), 4, 0)
        grid.addWidget(self.lbl_phase, 4, 1)
        grid.setColumnStretch(1, 1)
        root.addWidget(prog_box)

        # Report
        rep_box = QGroupBox("Report")
        rep_lay = QVBoxLayout(rep_box)
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        self.report_view.setPlaceholderText(
            "The final report of each run appears here (and is archived in "
            "<destination>/.applesync/reports/)."
        )
        btn_row = QHBoxLayout()
        self.btn_open_reports = QPushButton("Open the reports folder")
        self.btn_open_reports.clicked.connect(self._open_reports_dir)
        btn_row.addWidget(self.btn_open_reports)
        btn_row.addStretch(1)
        rep_lay.addWidget(self.report_view, stretch=1)
        rep_lay.addLayout(btn_row)
        root.addWidget(rep_box, stretch=1)

        # Update banner: hidden until there is something to report.
        self.update_bar = QFrame()
        self.update_bar.setFrameShape(QFrame.StyledPanel)
        self.update_bar.setStyleSheet(
            "QFrame { border: 1px solid #1a7f37; border-left: 8px solid #1a7f37; "
            "border-radius: 2px; padding: 4px; }"
        )
        up_lay = QHBoxLayout(self.update_bar)
        self.update_label = QLabel("")
        self.update_label.setWordWrap(True)
        self.btn_update = QPushButton("See the new version")
        up_lay.addWidget(self.update_label, stretch=1)
        up_lay.addWidget(self.btn_update)
        self.update_bar.hide()
        root.addWidget(self.update_bar)

        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Ready. AppleSync {__version__}")

    # ------------------------------------------------------------------ helpers
    def _dest_text(self) -> str:
        d = self.config.destination
        return str(d) if d else "⚠ No destination chosen."

    def _dest_ok(self) -> bool:
        d = self.config.destination
        return d is not None and d.exists()

    def _engine(self) -> SyncEngine:
        layout = make_layout(
            self.config.get("layout", "mirror"),
            bool(self.config.get("screenshots_apart", False)),
        )
        return SyncEngine(self.backend, self.config.destination, layout)

    def _on_layout_changed(self, *_):
        kind = self.layout_combo.currentData()
        self.config.set("layout", kind)
        self.config.set("screenshots_apart", self.screenshots_check.isChecked())
        self.screenshots_check.setEnabled(
            kind == "date" and self.layout_combo.isEnabled()
        )
        self.prepared = None
        if self.ui_state == UiState.PLAN_READY:
            self._set_ui_state(UiState.IDLE)

    def _refresh_layout_lock(self) -> None:
        """Reflect the destination's frozen layout in the options."""
        locked = None
        if self._dest_ok():
            try:
                with Manifest(self.config.destination) as m:
                    locked = m.locked_layout()
            except Exception:
                locked = None
        if locked is None:
            self.layout_combo.setEnabled(True)
            self.screenshots_check.setEnabled(
                self.layout_combo.currentData() == "date"
            )
            self.layout_lock_label.setText("(frozen at the first sync)")
            return
        kind = "date" if locked.startswith("date") else locked
        idx = self.layout_combo.findData(kind)
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.screenshots_check.setChecked(locked == "date+screenshots")
        self.config.set("layout", kind)
        self.config.set("screenshots_apart", locked == "date+screenshots")
        self.layout_combo.setEnabled(False)
        self.screenshots_check.setEnabled(False)
        self.layout_lock_label.setText(
            f"FROZEN for this destination: {label_for(locked)}"
        )

    def _set_ui_state(self, state: UiState) -> None:
        self.ui_state = state
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        ready = self.device_state == DeviceState.READY
        idle = self.ui_state in (UiState.IDLE, UiState.PLAN_READY)
        busy = not idle
        self.btn_inventory.setEnabled(ready and idle and self._dest_ok())
        self.btn_sync.setEnabled(
            ready and self.ui_state == UiState.PLAN_READY and self.prepared is not None
        )
        self.btn_verify.setEnabled(ready and idle and self._dest_ok())
        self.btn_stability.setEnabled(ready and idle)
        # Duplicates: reads the local manifest, no device required.
        self.btn_duplicates.setEnabled(idle and self._dest_ok())
        # Albums: separate from the backup (device + destination required).
        self.btn_albums.setEnabled(ready and idle and self._dest_ok())
        self.btn_cancel.setEnabled(busy)
        self.dest_btn.setEnabled(idle)

    def _show_error(self, message: str, details: str = "") -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("AppleSync — failure")
        box.setText(message)
        if details:
            box.setDetailedText(details)
        box.exec()

    def _show_last_run_summary(self) -> None:
        if not self._dest_ok():
            return
        try:
            with Manifest(self.config.destination) as m:
                last = m.last_completed_run()
            if last:
                _run_id, _, finished, inv_n, inv_b, cop_n, cop_b = last
                self._expected_files = inv_n or None
                import time as _t

                self.plan_label.setText(
                    f"Last completed sync: "
                    f"{_t.strftime('%Y-%m-%d %H:%M', _t.localtime(finished))} — "
                    f"inventory {inv_n} files ({fmt_bytes(inv_b or 0)}), "
                    f"{cop_n} copied ({fmt_bytes(cop_b or 0)}). "
                    f"Run \"1. Inventory\" to see the current delta."
                )
        except Exception:
            pass  # no manifest yet: first use

    # ------------------------------------------------------------------ device
    def _on_device_state(self, state: DeviceState, udid: str) -> None:
        self.device_state = state
        self.current_udid = udid
        color, title, hint = STATE_STYLES[state]
        label = title
        if udid:
            label += f"   ({udid})"
        self.state_label.setText(label)
        self.state_hint.setText(hint)
        self.banner.setStyleSheet(
            f"QFrame {{ border: 1px solid {color}; border-left: 8px solid {color}; "
            f"border-radius: 2px; padding: 4px; }}"
        )
        if state != DeviceState.READY and self.ui_state == UiState.PLAN_READY:
            # The device vanished between inventory and validation: the plan
            # stays on screen but the sync will have to be prepared again.
            self.statusBar().showMessage(
                "Device no longer available — plug it back in and run the "
                "inventory again."
            )
        self._refresh_buttons()

    # ------------------------------------------------------------------ dest
    def _choose_dest(self) -> None:
        start = str(self.config.destination or Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the backup folder", start
        )
        if chosen:
            self.config.destination = Path(chosen)
            self.dest_label.setText(self._dest_text())
            self.prepared = None
            self._set_ui_state(UiState.IDLE)
            self._refresh_layout_lock()
            self._show_last_run_summary()

    def _open_reports_dir(self) -> None:
        if not self._dest_ok():
            self._show_error("No destination chosen.")
            return
        d = self.config.destination / ".applesync" / "reports"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606 — opening the file manager is intended

    # ------------------------------------------------------------------ prepare
    def _start_prepare(self) -> None:
        if not self._dest_ok():
            self._show_error("Choose a destination folder first.")
            return
        self.cancel.clear()
        self.prepared = None
        self.plan_label.setText("Inventory running…")
        self._set_ui_state(UiState.PREPARING)
        self.watcher.paused = True
        w = PrepareWorker(self._engine(), self.current_udid, self.cancel, self)
        w.phase.connect(self._on_phase)
        w.inventory_progress.connect(self._on_inventory_progress)
        w.done.connect(self._on_prepared)
        w.failed.connect(self._on_worker_failed)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_inventory_progress(self, n: int, phase: str) -> None:
        self.lbl_phase.setText(f"Inventory — {phase}: {n} files seen")
        self.lbl_counts.setText(f"{n} files seen")
        # Bar scaled on the last known inventory count: each pass of the double
        # enumeration fills one half. Otherwise: busy animation.
        expected = self._expected_files
        if expected:
            if "1/2" in phase:
                base, width = 0, 500
            elif "2/2" in phase:
                base, width = 500, 500
            else:
                base, width = 0, 1000
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(
                min(1000, base + int(width * min(n, expected) / expected))
            )
        else:
            self._bar_busy()

    def _on_prepared(self, prepared: PreparedRun) -> None:
        self.prepared = prepared
        self._expected_files = prepared.inventory.count
        self._bar_done(True)
        inv, plan = prepared.inventory, prepared.plan
        lines = [
            f"Device: {prepared.device_label}",
            f"Inventory: {inv.count} files — {fmt_bytes(inv.total_bytes)} "
            f"(double enumeration matched ✓, {fmt_duration(inv.duration_s)})",
            f"Fingerprint: {inv.fingerprint()[:20]}…",
            "",
            f"-> To copy: {len(plan.to_copy)} files — "
            f"{fmt_bytes(sum(f.size for f in plan.to_copy))}",
            f"-> Already synchronised: {len(plan.already_synced)}",
        ]
        if plan.to_adopt:
            lines.append(
                f"-> Already on disk, to be re-recorded (adoption): "
                f"{len(plan.to_adopt)}"
            )
        if plan.conflicts:
            lines.append(
                f"-> ⚠ Conflicts (copied under a versioned name, never an "
                f"overwrite): {len(plan.conflicts)}"
            )
            lines.extend(
                f"      {c.remote.path} -> {c.versioned_path}"
                for c in plan.conflicts[:8]
            )
            if len(plan.conflicts) > 8:
                lines.append(f"      … and {len(plan.conflicts) - 8} more")
        if plan.missing_on_device:
            lines.append(
                f"-> Gone from the device since the last sync (KEPT on the PC): "
                f"{len(plan.missing_on_device)}"
            )
            lines.extend(
                f"      {e.source_path}" for e in plan.missing_on_device[:8]
            )
            if len(plan.missing_on_device) > 8:
                lines.append(f"      … and {len(plan.missing_on_device) - 8} more")
        if self.config.get("layout", "mirror") == "archive":
            lines.append("")
            lines.append(
                "Archive layout: the capture date (EXIF) is read during the "
                "copy — every file is filed and renamed after it (mtime as "
                "fallback), Live Photos go to _LivePhotos, content duplicates "
                "to _Duplicates."
            )
        if prepared.breakdown_csv is not None:
            lines.append("")
            lines.append(
                f"Month x extension breakdown exported: {prepared.breakdown_csv}"
            )
        lines.append("")
        lines.append("Validate by clicking \"2. Synchronise\".")
        self.plan_label.setText("\n".join(lines))
        self.lbl_phase.setText("Plan ready — waiting for validation.")
        self.statusBar().showMessage(
            "Plan ready. Validate with \"2. Synchronise\" — nothing written yet."
        )
        self.watcher.paused = False
        self._set_ui_state(UiState.PLAN_READY)

    # ------------------------------------------------------------------ execute
    def _start_execute(self) -> None:
        if self.prepared is None:
            return
        self.cancel.clear()
        self._set_ui_state(UiState.SYNCING)
        self.watcher.paused = True
        w = ExecuteWorker(self._engine(), self.prepared, self.cancel, True, self)
        w.phase.connect(self._on_phase)
        w.progress.connect(self._on_progress)
        w.verify_progress.connect(self._on_verify_progress)
        w.done.connect(self._on_report)
        w.failed.connect(self._on_worker_failed)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    # -- progress bar: never frozen while something is running ---------------
    # Convention: a phase change puts the bar in "busy" mode (continuous
    # animation) as long as no counter is available; as soon as numbers come
    # in, the bar becomes a real percentage.

    def _bar_busy(self) -> None:
        self.progress_bar.setRange(0, 0)          # "in progress" animation

    def _bar_ratio(self, i: int, n: int) -> None:
        if self.progress_bar.maximum() != 1000:
            self.progress_bar.setRange(0, 1000)
        if n > 0:
            self.progress_bar.setValue(min(1000, int(1000 * i / n)))

    def _bar_done(self, success: bool) -> None:
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000 if success else 0)

    def _on_phase(self, phase: str) -> None:
        self.lbl_phase.setText(phase)
        self.statusBar().showMessage(phase)
        if self.ui_state != UiState.IDLE:
            self._bar_busy()

    def _on_progress(self, s: ProgressSnapshot) -> None:
        if s.bytes_total > 0:
            self._bar_ratio(s.bytes_done, s.bytes_total)
        self.lbl_file.setText(s.current_file or "—")
        self.lbl_counts.setText(
            f"{s.files_done} / {s.files_total} files — "
            f"{fmt_bytes(s.bytes_done)} / {fmt_bytes(s.bytes_total)}"
        )
        eta = s.eta_s
        self.lbl_speed.setText(
            f"{fmt_bytes(int(s.bytes_per_s))}/s"
            + (f" — ETA {fmt_duration(eta)}" if eta is not None else "")
        )

    def _on_verify_progress(self, i: int, n: int, path: str) -> None:
        """Progress of a disk re-read (verification, adoption)."""
        self._bar_ratio(i, n)
        self.lbl_file.setText(path or "—")
        self.lbl_counts.setText(f"{i} / {n} files re-read from disk")
        self.lbl_speed.setText("—")

    def _on_report(self, report: RunReport) -> None:
        self.report_view.setMarkdown(report.to_markdown())
        self.prepared = None
        self.watcher.paused = False
        self._bar_done(report.status == COMPLETED)
        self._refresh_layout_lock()   # the first sync just froze the layout
        self._set_ui_state(UiState.IDLE)
        titles = {
            COMPLETED: "Synchronisation completed and verified.",
            INTERRUPTED: "Synchronisation interrupted — it can be resumed.",
            FAILED: "SYNCHRONISATION FAILED — read the report.",
        }
        self.lbl_phase.setText(titles.get(report.status, report.status))
        self.statusBar().showMessage(titles.get(report.status, report.status))
        if report.status != COMPLETED:
            self._show_error(
                titles.get(report.status, report.status)
                + ("\n\n" + (report.error or "") if report.error else "")
            )

    # ------------------------------------------------------------------ verify
    def _start_verify(self) -> None:
        if not self._dest_ok():
            return
        self.cancel.clear()
        self._set_ui_state(UiState.VERIFYING)
        self.watcher.paused = True
        self.progress_bar.setValue(0)
        w = VerifyWorker(
            self.backend, self.current_udid, self.config.destination,
            self.cancel, True, self
        )
        w.phase.connect(self._on_phase)
        w.progress.connect(self._on_verify_progress)
        w.done.connect(self._on_verify_done)
        w.failed.connect(self._on_worker_failed)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_verify_done(self, rep: VerificationReport) -> None:
        self.watcher.paused = False
        self._bar_done(rep.ok)
        self._set_ui_state(UiState.IDLE)
        lines = [
            "# Destination verification",
            "",
            f"- Files checked: {rep.checked_count}",
            f"- Re-read and hashed: {rep.hashed_count}",
            f"- Conforming: {rep.ok_count}",
        ]
        if rep.ok:
            lines.append(
                "- **No discrepancy: the destination is faithful to the device.**"
            )
        else:
            lines.append(f"- **DISCREPANCIES: {len(rep.discrepancies)}**")
            lines.extend(
                f"  - `{d.source_path}` [{d.kind}] {d.detail}"
                for d in rep.discrepancies
            )
        self.report_view.setMarkdown("\n".join(lines))
        self.lbl_phase.setText(
            "Verification: no discrepancy." if rep.ok
            else f"Verification: {len(rep.discrepancies)} DISCREPANCY(IES) — "
                 f"see the report."
        )
        if not rep.ok:
            self._show_error(
                f"{len(rep.discrepancies)} discrepancy(ies) between the device "
                f"and the destination. Full list in the Report panel. "
                f"DELETE NOTHING on the device."
            )

    # ------------------------------------------------------------------ duplicates
    def _show_duplicates(self) -> None:
        """CONTENT duplicates (SHA-256 from the manifest). Local, fast."""
        if not self._dest_ok():
            return
        from applesync.core.duplicates import find_duplicates

        try:
            with Manifest(self.config.destination) as m:
                report = find_duplicates(m)
        except Exception as e:
            self._show_error(f"Cannot read the manifest: {e}")
            return
        self.report_view.setMarkdown(report.to_markdown())
        if report.scanned_count == 0:
            self.lbl_phase.setText(
                "Duplicates: empty manifest — run a synchronisation first."
            )
        elif report.groups:
            self.lbl_phase.setText(
                f"Duplicates: {len(report.groups)} group(s), "
                f"{report.duplicate_count} surplus copy(ies) "
                f"({fmt_bytes(report.wasted_bytes)}) — nothing is deleted."
            )
        else:
            self.lbl_phase.setText(
                f"Duplicates: none (across {report.scanned_count} hashed files)."
            )

    # ------------------------------------------------------------------ albums
    def _start_albums(self) -> None:
        """Separate from the backup: recover the device albums (Photos
        database) as folders of copies of the already-backed-up files."""
        if not self._dest_ok():
            return
        self.cancel.clear()
        self._set_ui_state(UiState.ALBUMS)
        self.watcher.paused = True
        self.progress_bar.setValue(0)
        from applesync.ui.workers import AlbumsWorker

        w = AlbumsWorker(
            self.backend, self.current_udid, self.config.destination,
            self.cancel, self
        )
        w.phase.connect(self._on_phase)
        w.progress.connect(self._on_albums_progress)
        w.mat_progress.connect(self._on_albums_mat_progress)
        w.done.connect(self._on_albums_done)
        w.failed.connect(self._on_worker_failed)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_albums_progress(self, done: int, total: int) -> None:
        self._bar_ratio(done, total)
        self.lbl_file.setText("PhotoData/Photos.sqlite")
        self.lbl_counts.setText(
            f"{fmt_bytes(done)} / {fmt_bytes(total)} (Photos database)"
        )

    def _on_albums_mat_progress(self, i: int, n: int) -> None:
        self._bar_ratio(i, n)
        self.lbl_file.setText("—")
        self.lbl_counts.setText(f"{i} / {n} album files copied")

    def _on_albums_done(self, report, report_path: str) -> None:
        self.watcher.paused = False
        self._bar_done(not report.unmatched)
        self._set_ui_state(UiState.IDLE)
        self.report_view.setMarkdown(report.to_markdown())
        state = (
            f"Albums: {report.albums_count} folders, "
            f"{report.copies_created} files copied "
            f"({fmt_bytes(report.copied_bytes)}), "
            f"{report.favorites_count} favourites"
        )
        if report.unmatched:
            state += f" — {len(report.unmatched)} unmatched (see the report)"
        self.lbl_phase.setText(state)
        self.statusBar().showMessage(f"Albums report: {report_path}")

    # ------------------------------------------------------------------ stability
    def _start_stability(self) -> None:
        self.cancel.clear()
        self._set_ui_state(UiState.STABILITY)
        self.watcher.paused = True
        self.report_view.setMarkdown(
            "# Stability check running\n\nThree full inventories will be taken. "
            "Between each pass, follow the instruction shown in the status bar "
            "(unplug then plug the device back in)."
        )
        w = StabilityWorker(
            self.backend, self.current_udid, self.cancel,
            simulate=self.simulate, rounds=3, parent=self
        )
        w.instruction.connect(self._on_phase)
        w.inventory_progress.connect(self._on_inventory_progress)
        w.done.connect(self._on_stability_done)
        w.failed.connect(self._on_worker_failed)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_stability_done(self, result: StabilityResult) -> None:
        self.watcher.paused = False
        self._bar_done(result.stable)
        self._set_ui_state(UiState.IDLE)
        lines = ["# Stability check (success criterion)", ""]
        for r in result.rounds:
            lines.append(
                f"- Pass {r.index}: {r.count} files, {fmt_bytes(r.total_bytes)}, "
                f"fingerprint `{r.fingerprint[:20]}…` ({fmt_duration(r.duration_s)})"
            )
        lines.append("")
        lines.append("```")
        lines.append(result.verdict())
        lines.append("```")
        self.report_view.setMarkdown("\n".join(lines))
        self.lbl_phase.setText(
            "Stability check: STABLE ✓" if result.stable
            else "Stability check: UNSTABLE — see the report."
        )

    # ------------------------------------------------------------------ common
    def _on_worker_failed(self, message: str, details: str) -> None:
        self.watcher.paused = False
        self._bar_done(False)
        self._set_ui_state(UiState.IDLE)
        self.lbl_phase.setText(f"Failure: {message}")
        self._show_error(message, details)

    def _request_cancel(self) -> None:
        self.cancel.set()
        self.lbl_phase.setText("Stop requested — shutting down cleanly…")

    def _forget_worker(self, w) -> None:
        if w in self._workers:
            self._workers.remove(w)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.cancel.set()
        self.watcher.stop()
        self.watcher.wait(3000)
        for w in list(self._workers):
            w.wait(5000)
        shutdown = getattr(self.backend, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)
