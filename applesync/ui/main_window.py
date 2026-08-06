"""AppleSync main window.

Window state machine:
    IDLE -> PREPARING -> PLAN_READY -> SYNCING -> IDLE
                                    \\ clean interruption /
    (VERIFYING, STABILITY and ALBUMS are exclusive operations from IDLE)

Shape of the screen: a header carrying the app identity and a live device
chip, then the destination, then the three steps of the flow as numbered
cards, then progress, then the report. Occasional tools (albums, duplicates,
stability check) live in a menu instead of competing with the flow.

Presentation rules: no widget carries inline colours — it declares an object
name or a dynamic property and `theme.py` decides how that looks. Numbers are
monospaced so they stop shifting while a transfer runs.
"""

from __future__ import annotations

import os
import threading
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from applesync import __version__
from applesync.core.config import Config
from applesync.core.engine import (
    COMPLETED,
    FAILED,
    INTERRUPTED,
    PreparedRun,
    ProgressSnapshot,
    SyncEngine,
)
from applesync.core.layout import label_for, make_layout
from applesync.core.manifest import Manifest
from applesync.core.report import RunReport, fmt_bytes, fmt_duration
from applesync.core.stability import StabilityResult
from applesync.core.verifier import VerificationReport
from applesync.device.base import DeviceBackend, DeviceState
from applesync.ui import theme
from applesync.ui.workers import (
    DeviceWatcher,
    ExecuteWorker,
    PrepareWorker,
    StabilityWorker,
    UpdateCheckWorker,
    VerifyWorker,
)

# tone drives the chip colours through the stylesheet
STATE_STYLES = {
    DeviceState.ABSENT: ("neutral", "No device detected",
                         "The Apple service is answering but sees no device. "
                         "Plug the iPhone in over USB; if the cable is already "
                         "connected, try another cable (many are charge-only) "
                         "or another port, and unlock the device."),
    DeviceState.NO_USBMUXD: ("danger", "Apple service unreachable",
                             "The usbmuxd driver is not answering on "
                             "127.0.0.1:27015, so this PC cannot see any "
                             "device even plugged in. Install iTunes, the "
                             "Apple Devices app or the CopyTrans drivers — or "
                             "start the Apple Mobile Device Service."),
    DeviceState.LOCKED: ("warn", "Device locked",
                         "Unlock the device screen with its passcode or Face ID."),
    DeviceState.UNTRUSTED: ("warn", "Device not paired",
                            "On the device, tap \"Trust This Computer\" and "
                            "enter the passcode."),
    DeviceState.READY: ("ready", "Device ready",
                        "Run the inventory when you are ready."),
    DeviceState.ERROR: ("danger", "Device unreachable",
                        "Unplug and plug the cable back in. If it persists, "
                        "restart the Apple Mobile Device service."),
}

ASSETS = Path(__file__).with_name("assets")


class UiState(Enum):
    IDLE = auto()
    PREPARING = auto()
    PLAN_READY = auto()
    SYNCING = auto()
    VERIFYING = auto()
    STABILITY = auto()
    ALBUMS = auto()


def _hsep() -> QFrame:
    f = QFrame()
    f.setObjectName("HSep")
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    return f


class MainWindow(QMainWindow):
    def __init__(self, backend: DeviceBackend, simulate: bool = False,
                 config: Config | None = None):
        super().__init__()
        self.backend = backend
        self.simulate = simulate
        self.config = config or Config()
        self.cancel = threading.Event()
        self.prepared: PreparedRun | None = None
        self._expected_files: int | None = None
        self._last_error_details: str = ""
        self.current_udid: str = ""
        self.device_state: DeviceState = DeviceState.ABSENT
        self.ui_state = UiState.IDLE
        self._workers: list = []
        self._theme_mode: str = self.config.get("theme", "system")
        self.palette_ = theme.palette_for(self._theme_mode)

        title = "AppleSync"
        if simulate:
            title += " — simulation mode, no real device"
        self.setWindowTitle(title)
        icon = ASSETS / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(860, 680)
        self.resize(1000, 880)
        self._build()
        self._apply_theme()

        self.watcher = DeviceWatcher(backend)
        self.watcher.state_changed.connect(self._on_device_state)
        self.watcher.start()
        self._refresh_layout_lock()
        self._refresh_buttons()
        self._show_last_run_summary()
        self._start_update_check()

    # ------------------------------------------------------------------ theme
    def _apply_theme(self) -> None:
        self.palette_ = theme.palette_for(self._theme_mode)
        self.setStyleSheet(theme.stylesheet(self.palette_))
        self.report_view.document().setDefaultStyleSheet(
            theme.report_document_css(self.palette_)
        )
        # Re-render the current document so the new colours take effect.
        self._set_report(self.report_view.toMarkdown())

    def _set_theme(self, mode: str) -> None:
        self._theme_mode = mode
        self.config.set("theme", mode)
        self._apply_theme()

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
            f"Version {info.latest} is available — you are running "
            f"{info.current}. Updating is manual."
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
        root.setContentsMargins(20, 16, 20, 10)
        root.setSpacing(theme.GAP)

        root.addWidget(self._build_header())
        root.addWidget(self._build_notices())
        root.addWidget(self._build_destination())
        root.addWidget(self._build_flow())
        root.addWidget(self._build_progress())
        root.addWidget(self._build_report(), stretch=1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

    def _build_header(self) -> QWidget:
        head = QWidget()
        head.setObjectName("Header")
        row = QHBoxLayout(head)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(10)

        name = QLabel("AppleSync")
        name.setObjectName("AppName")
        version = QLabel(f"v{__version__}")
        version.setObjectName("AppVersion")
        row.addWidget(name)
        row.addWidget(version)
        row.addSpacing(14)

        # Device chip — the live state, always visible, never in the way.
        self.chip = QFrame()
        self.chip.setObjectName("Chip")
        self.chip.setProperty("state", "neutral")
        chip_row = QHBoxLayout(self.chip)
        chip_row.setContentsMargins(12, 6, 14, 6)
        chip_row.setSpacing(8)
        self.chip_dot = QLabel()
        self.chip_dot.setObjectName("ChipDot")
        # state_label carries the state title: the smoke test reads it.
        self.state_label = QLabel("Looking for a device…")
        self.state_label.setObjectName("ChipText")
        chip_row.addWidget(self.chip_dot)
        chip_row.addWidget(self.state_label)
        row.addWidget(self.chip)
        row.addStretch(1)

        self.tools_btn = QToolButton()
        self.tools_btn.setText("Tools  ▾")
        self.tools_btn.setPopupMode(QToolButton.InstantPopup)
        self.tools_btn.setMenu(self._build_tools_menu())
        row.addWidget(self.tools_btn)
        return head

    def _build_tools_menu(self) -> QMenu:
        menu = QMenu(self)

        self.act_albums = QAction("Rebuild albums and favourites", self)
        self.act_albums.setToolTip(
            "Read the device Photos database and rebuild each album as a "
            "folder of copies"
        )
        self.act_albums.triggered.connect(self._start_albums)
        menu.addAction(self.act_albums)

        self.act_duplicates = QAction("List content duplicates", self)
        self.act_duplicates.setToolTip(
            "Group files with identical content — report only, nothing deleted"
        )
        self.act_duplicates.triggered.connect(self._show_duplicates)
        menu.addAction(self.act_duplicates)

        self.act_stability = QAction("Run stability check (3 inventories)", self)
        self.act_stability.setToolTip(
            "Three inventories with an unplug in between — proves enumeration "
            "is reproducible. A one-off diagnostic."
        )
        self.act_stability.triggered.connect(self._start_stability)
        menu.addAction(self.act_stability)

        menu.addSeparator()
        self.act_open_reports = QAction("Open reports folder", self)
        self.act_open_reports.triggered.connect(self._open_reports_dir)
        menu.addAction(self.act_open_reports)

        menu.addSeparator()
        appearance = menu.addMenu("Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label in (("system", "Follow system"),
                            ("light", "Light"),
                            ("dark", "Dark")):
            act = QAction(label, self, checkable=True)
            act.setChecked(self._theme_mode == mode)
            act.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            group.addAction(act)
            appearance.addAction(act)
        return menu

    def _build_notices(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("Plain")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.update_bar = QFrame()
        self.update_bar.setObjectName("Card")
        self.update_bar.setProperty("tone", "accent")
        up = QHBoxLayout(self.update_bar)
        up.setContentsMargins(14, 10, 10, 10)
        self.update_label = QLabel("")
        self.update_label.setObjectName("NoticeText")
        self.update_label.setWordWrap(True)
        self.btn_update = QPushButton("Release notes")
        up.addWidget(self.update_label, stretch=1)
        up.addWidget(self.btn_update)
        self.update_bar.hide()
        lay.addWidget(self.update_bar)

        # Errors surface inline first; the modal only carries the traceback.
        self.error_bar = QFrame()
        self.error_bar.setObjectName("Card")
        self.error_bar.setProperty("tone", "danger")
        er = QHBoxLayout(self.error_bar)
        er.setContentsMargins(14, 10, 10, 10)
        er.setSpacing(8)
        self.error_label = QLabel("")
        self.error_label.setObjectName("NoticeText")
        self.error_label.setWordWrap(True)
        self.btn_error_details = QPushButton("Details")
        self.btn_error_details.clicked.connect(self._show_error_details)
        self.btn_error_dismiss = QPushButton("Dismiss")
        self.btn_error_dismiss.setObjectName("Quiet")
        self.btn_error_dismiss.clicked.connect(self.error_bar.hide)
        er.addWidget(self.error_label, stretch=1)
        er.addWidget(self.btn_error_details)
        er.addWidget(self.btn_error_dismiss)
        self.error_bar.hide()
        lay.addWidget(self.error_bar)
        return holder

    def _build_destination(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(theme.PAD, 12, theme.PAD, 12)
        v.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        cap = QLabel("BACKUP FOLDER")
        cap.setObjectName("SectionLabel")
        row.addWidget(cap)
        row.addStretch(1)
        self.dest_btn = QPushButton("Choose…")
        self.dest_btn.setObjectName("Quiet")
        self.dest_btn.clicked.connect(self._choose_dest)
        row.addWidget(self.dest_btn)
        v.addLayout(row)

        self.dest_label = QLabel(self._dest_text())
        self.dest_label.setObjectName("Value")
        self.dest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dest_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.dest_label)

        opts = QHBoxLayout()
        opts.setSpacing(10)
        lab = QLabel("LAYOUT")
        lab.setObjectName("FieldLabel")
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Mirror of the device tree", "mirror")
        self.layout_combo.addItem("By date — YYYY / YYYY-MM", "date")
        self.layout_combo.addItem("Archive — dated names, _LivePhotos", "archive")
        self.screenshots_check = QCheckBox("Screenshots apart")
        self.layout_lock_label = QLabel("")
        self.layout_lock_label.setObjectName("LockNote")
        kind = self.config.get("layout", "mirror")
        idx = self.layout_combo.findData(kind)
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.screenshots_check.setChecked(
            bool(self.config.get("screenshots_apart", False))
        )
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self.screenshots_check.toggled.connect(self._on_layout_changed)
        opts.addWidget(lab)
        opts.addWidget(self.layout_combo)
        opts.addWidget(self.screenshots_check)
        opts.addWidget(self.layout_lock_label, stretch=1)
        v.addLayout(opts)
        return card

    def _step_header(self, number: str, title: str, button: QPushButton) -> QWidget:
        head = QWidget()
        head.setObjectName("Plain")
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        num = QLabel(number)
        num.setObjectName("StepNumber")
        lab = QLabel(title)
        lab.setObjectName("CardTitle")
        row.addWidget(num)
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(button)
        return head

    def _build_flow(self) -> QWidget:
        """The three steps that matter, in order, in one card."""
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(theme.PAD, 12, theme.PAD, 12)
        v.setSpacing(10)

        self.btn_inventory = QPushButton("Run inventory")
        self.btn_inventory.setObjectName("Primary")
        self.btn_inventory.clicked.connect(self._start_prepare)
        self.btn_sync = QPushButton("Copy to PC")
        self.btn_sync.setObjectName("Primary")
        self.btn_sync.clicked.connect(self._start_execute)
        self.btn_verify = QPushButton("Verify")
        self.btn_verify.clicked.connect(self._start_verify)
        self.btn_cancel = QPushButton("Stop")
        self.btn_cancel.setObjectName("Stop")
        self.btn_cancel.clicked.connect(self._request_cancel)

        v.addWidget(self._step_header("01", "Inventory the device",
                                      self.btn_inventory))

        # Plan: empty state, then the numbers, scrolling inside the card.
        self.plan_empty_title = QLabel("Nothing inventoried yet")
        self.plan_empty_title.setObjectName("EmptyTitle")
        self.plan_empty_body = QLabel(
            "The inventory walks the device twice and compares both passes. "
            "Nothing is written until you validate the plan."
        )
        self.plan_empty_body.setObjectName("EmptyBody")
        self.plan_empty_body.setWordWrap(True)
        self.plan_label = QLabel("")
        self.plan_label.setObjectName("PlanText")   # monospaced via the QSS
        # No wrapping: a wrapped line would break the column grid, and a long
        # path would silently change the block's height. It scrolls instead.
        self.plan_label.setWordWrap(False)
        self.plan_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.plan_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.plan_label.hide()

        self.plan_scroll = QScrollArea()
        self.plan_scroll.setObjectName("Plain")
        self.plan_scroll.setWidgetResizable(True)
        self.plan_scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("Plain")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(24, 0, 0, 0)
        inner_lay.setSpacing(5)
        inner_lay.addWidget(self.plan_empty_title)
        inner_lay.addWidget(self.plan_empty_body)
        inner_lay.addWidget(self.plan_label)
        inner_lay.addStretch(1)
        self.plan_scroll.setWidget(inner)
        self.plan_scroll.setMinimumHeight(142)
        v.addWidget(self.plan_scroll)

        v.addWidget(_hsep())
        v.addWidget(self._step_header("02", "Copy what is missing",
                                      self.btn_sync))
        v.addWidget(_hsep())
        v.addWidget(self._step_header("03", "Verify the backup",
                                      self.btn_verify))
        return card

    def _build_progress(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(theme.PAD, 12, theme.PAD, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)

        self.lbl_phase = QLabel("Waiting.")
        self.lbl_phase.setObjectName("Phase")
        self.lbl_phase.setWordWrap(True)
        self.lbl_file = QLabel("—")
        self.lbl_file.setObjectName("ValueMuted")
        self.lbl_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_file.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_counts = QLabel("—")
        self.lbl_counts.setObjectName("Value")
        self.lbl_speed = QLabel("—")
        self.lbl_speed.setObjectName("Value")

        # Stop belongs next to what is running, not inside a step.
        phase_row = QWidget()
        phase_row.setObjectName("Plain")
        pr = QHBoxLayout(phase_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(10)
        pr.addWidget(self.lbl_phase, stretch=1)
        pr.addWidget(self.btn_cancel)

        grid.addWidget(self.progress_bar, 0, 0, 1, 3)
        grid.addWidget(phase_row, 1, 0, 1, 3)
        for col, (name, widget) in enumerate((
            ("FILE", self.lbl_file),
            ("DONE", self.lbl_counts),
            ("RATE", self.lbl_speed),
        )):
            cap = QLabel(name)
            cap.setObjectName("FieldLabel")
            holder = QWidget()
            holder.setObjectName("Cell")
            cell = QVBoxLayout(holder)
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(4)
            cell.addWidget(cap)
            cell.addWidget(widget)
            grid.addWidget(holder, 2, col)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        return card

    def _build_report(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("Plain")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        cap = QLabel("REPORT")
        cap.setObjectName("SectionLabel")
        lay.addWidget(cap)
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        self.report_view.setPlaceholderText(
            "The report of each run appears here, and is archived in "
            "<destination>/.applesync/reports/."
        )
        self.report_view.setMinimumHeight(150)
        lay.addWidget(self.report_view, stretch=1)
        return holder

    # ------------------------------------------------------------------ helpers
    def _dest_text(self) -> str:
        d = self.config.destination
        return str(d) if d else "No folder chosen yet"

    def _dest_ok(self) -> bool:
        d = self.config.destination
        return d is not None and d.exists()

    def _engine(self) -> SyncEngine:
        layout = make_layout(
            self.config.get("layout", "mirror"),
            bool(self.config.get("screenshots_apart", False)),
        )
        return SyncEngine(self.backend, self.config.destination, layout)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        """Re-apply the stylesheet after a dynamic property changed."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_report(self, markdown: str) -> None:
        """Render a Markdown report in the panel.

        Qt's Markdown importer marks headings with a FontSizeAdjustment (+3 on
        an h1, i.e. double size) which takes precedence over any point size a
        stylesheet or a merged format could ask for. The heading format is
        therefore replaced outright with the type scale, so a report title
        reads as a title rather than as a banner."""
        self.report_view.setMarkdown(markdown)
        doc = self.report_view.document()
        cursor = QTextCursor(doc)
        block = doc.begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level:
                fmt = QTextCharFormat()
                fmt.setFontPointSize(
                    theme.SIZE_DISPLAY if level == 1 else theme.SIZE_BODY
                )
                fmt.setFontWeight(QFont.DemiBold)
                if level > 1:
                    fmt.setForeground(QColor(self.palette_.muted))
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + block.length() - 1, QTextCursor.KeepAnchor
                )
                cursor.setCharFormat(fmt)
            block = block.next()

    def _show_plan_text(self, text: str) -> None:
        self.plan_empty_title.hide()
        self.plan_empty_body.hide()
        self.plan_label.setText(text)
        self.plan_label.show()

    def _show_plan_empty(self, title: str, body: str) -> None:
        self.plan_label.hide()
        self.plan_empty_title.setText(title)
        self.plan_empty_body.setText(body)
        self.plan_empty_title.show()
        self.plan_empty_body.show()

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
            self.layout_lock_label.setText("locked once the first copy runs")
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
        self.layout_lock_label.setText(f"frozen: {label_for(locked)}")

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
        self.btn_cancel.setEnabled(busy)
        self.dest_btn.setEnabled(idle)
        # Tools: albums and the stability check need the device; duplicates
        # only read the local manifest.
        self.act_albums.setEnabled(ready and idle and self._dest_ok())
        self.act_stability.setEnabled(ready and idle)
        self.act_duplicates.setEnabled(idle and self._dest_ok())
        self.act_open_reports.setEnabled(self._dest_ok())
        self.tools_btn.setEnabled(idle)

    def _show_error(self, message: str, details: str = "") -> None:
        """Errors surface inline; the modal is opt-in, for the traceback."""
        self._last_error_details = details
        self.error_label.setText(message)
        self.btn_error_details.setVisible(bool(details))
        self.error_bar.show()

    def _show_error_details(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("AppleSync — failure")
        box.setText(self.error_label.text())
        if self._last_error_details:
            box.setDetailedText(self._last_error_details)
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

                self._show_plan_empty(
                    "Ready for the next run",
                    f"Last completed copy "
                    f"{_t.strftime('%d %b %Y at %H:%M', _t.localtime(finished))} — "
                    f"{inv_n} files inventoried ({fmt_bytes(inv_b or 0)}), "
                    f"{cop_n} copied ({fmt_bytes(cop_b or 0)}).",
                )
        except Exception:
            pass  # no manifest yet: first use

    # ------------------------------------------------------------------ device
    def _on_device_state(self, state: DeviceState, udid: str) -> None:
        self.device_state = state
        self.current_udid = udid
        tone, title, hint = STATE_STYLES[state]
        self.state_label.setText(title)
        self.chip.setToolTip(f"{hint}\n\n{udid}" if udid else hint)
        self.chip.setProperty("state", tone)
        self._repolish(self.chip)
        self._repolish(self.chip_dot)
        if state == DeviceState.READY:
            self.statusBar().showMessage(udid or "Device ready")
        else:
            self.statusBar().showMessage(hint)
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
            self._show_error("No backup folder chosen.")
            return
        d = self.config.destination / ".applesync" / "reports"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606 — opening the file manager is intended

    # ------------------------------------------------------------------ prepare
    def _start_prepare(self) -> None:
        if not self._dest_ok():
            self._show_error("Choose a backup folder first.")
            return
        self.cancel.clear()
        self.error_bar.hide()
        self.prepared = None
        self._show_plan_empty(
            "Inventory running",
            "Walking the device twice and comparing both passes. Nothing is "
            "written during this step.",
        )
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
        self.lbl_phase.setText(f"Inventory — {phase}")
        self.lbl_counts.setText(f"{n:,} files seen".replace(",", " "))
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

        # Every line starts on the left edge and pads to the right: alignment
        # then survives whatever the text engine does with leading spaces.
        def row(label: str, count: int, size: int | None = None,
                note: str = "") -> str:
            files = f"{count:,}".replace(",", " ") + " files"
            cell = f"{fmt_bytes(size):>11}" if size is not None else " " * 11
            return f"{label:<20}{files:>12}   {cell}   {note}".rstrip()

        lines = [
            row("On the device", inv.count, inv.total_bytes,
                f"both passes matched · {fmt_duration(inv.duration_s)}"),
            f"{'Fingerprint':<20}{inv.fingerprint()[:24]}…",
            "",
            row("To copy", len(plan.to_copy),
                sum(f.size for f in plan.to_copy)),
            row("Already backed up", len(plan.already_synced)),
        ]
        if plan.to_adopt:
            lines.append(row("Already on disk", len(plan.to_adopt), None,
                             "to be re-recorded in the manifest"))
        if plan.conflicts:
            lines.append(row("Conflicts", len(plan.conflicts), None,
                             "copied under a versioned name, never overwritten"))
            lines.extend(
                f"{'':<20}{c.remote.path} -> {c.versioned_path}"
                for c in plan.conflicts[:5]
            )
            if len(plan.conflicts) > 5:
                lines.append(f"{'':<20}… and {len(plan.conflicts) - 5} more")
        if plan.missing_on_device:
            lines.append(row("Gone from the device", len(plan.missing_on_device),
                             None, "kept on the PC"))
            lines.extend(
                f"{'':<20}{e.source_path}" for e in plan.missing_on_device[:5]
            )
            if len(plan.missing_on_device) > 5:
                lines.append(
                    f"{'':<20}… and {len(plan.missing_on_device) - 5} more"
                )
        if prepared.breakdown_csv is not None:
            lines.append("")
            lines.append(
                f"{'Breakdown exported':<20}"
                f".applesync/reports/{Path(prepared.breakdown_csv).name}"
            )
        self._show_plan_text("\n".join(lines))
        self.lbl_phase.setText("Plan ready — nothing written yet.")
        self.statusBar().showMessage("Plan ready — step 2 starts the copy.")
        self.watcher.paused = False
        self._set_ui_state(UiState.PLAN_READY)

    # ------------------------------------------------------------------ execute
    def _start_execute(self) -> None:
        if self.prepared is None:
            return
        self.cancel.clear()
        self.error_bar.hide()
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
    # A phase change puts the bar in "busy" mode (continuous animation) as long
    # as no counter is available; as soon as numbers come in, it becomes a real
    # percentage.

    def _bar_busy(self) -> None:
        self.progress_bar.setProperty("outcome", "")
        self._repolish(self.progress_bar)
        self.progress_bar.setRange(0, 0)

    def _bar_ratio(self, i: int, total: int) -> None:
        if self.progress_bar.maximum() != 1000:
            self.progress_bar.setRange(0, 1000)
        if total > 0:
            self.progress_bar.setValue(min(1000, int(1000 * i / total)))

    def _bar_done(self, success: bool) -> None:
        """Full bar either way — the colour says how it ended."""
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000)
        self.progress_bar.setProperty("outcome", "" if success else "failed")
        self._repolish(self.progress_bar)

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
            f"{s.files_done} / {s.files_total} files   "
            f"{fmt_bytes(s.bytes_done)} / {fmt_bytes(s.bytes_total)}"
        )
        eta = s.eta_s
        self.lbl_speed.setText(
            f"{fmt_bytes(int(s.bytes_per_s))}/s"
            + (f"   ETA {fmt_duration(eta)}" if eta is not None else "")
        )

    def _on_verify_progress(self, i: int, total: int, path: str) -> None:
        """Progress of a disk re-read (verification, adoption)."""
        self._bar_ratio(i, total)
        self.lbl_file.setText(path or "—")
        self.lbl_counts.setText(f"{i} / {total} files re-read")
        self.lbl_speed.setText("—")

    def _on_report(self, report: RunReport) -> None:
        self._set_report(report.to_markdown())
        self.prepared = None
        self.watcher.paused = False
        self._bar_done(report.status == COMPLETED)
        self._refresh_layout_lock()   # the first copy just froze the layout
        self._set_ui_state(UiState.IDLE)
        titles = {
            COMPLETED: "Synchronisation completed and verified.",
            INTERRUPTED: "Synchronisation interrupted — it can be resumed.",
            FAILED: "Synchronisation failed — read the report.",
        }
        self.lbl_phase.setText(titles.get(report.status, report.status))
        self.statusBar().showMessage(titles.get(report.status, report.status))
        if report.status != COMPLETED:
            self._show_error(
                titles.get(report.status, report.status)
                + (" " + (report.error or "") if report.error else "")
            )

    # ------------------------------------------------------------------ verify
    def _start_verify(self) -> None:
        if not self._dest_ok():
            return
        self.cancel.clear()
        self.error_bar.hide()
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
            "# Backup verification",
            "",
            f"- Files checked: {rep.checked_count}",
            f"- Re-read and hashed: {rep.hashed_count}",
            f"- Conforming: {rep.ok_count}",
        ]
        if rep.ok:
            lines.append(
                "- **No discrepancy: the backup is faithful to the device.**"
            )
        else:
            lines.append(f"- **DISCREPANCIES: {len(rep.discrepancies)}**")
            lines.extend(
                f"  - `{d.source_path}` [{d.kind}] {d.detail}"
                for d in rep.discrepancies
            )
        self._set_report("\n".join(lines))
        self.lbl_phase.setText(
            "Verification: no discrepancy." if rep.ok
            else f"Verification: {len(rep.discrepancies)} discrepancy(ies) — "
                 f"see the report."
        )
        if not rep.ok:
            self._show_error(
                f"{len(rep.discrepancies)} discrepancy(ies) between the device "
                f"and the backup. Full list in the report below. Delete nothing "
                f"on the device."
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
        self._set_report(report.to_markdown())
        if report.scanned_count == 0:
            self.lbl_phase.setText(
                "Duplicates: empty manifest — copy something first."
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
        self.error_bar.hide()
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
        self.lbl_counts.setText(f"{fmt_bytes(done)} / {fmt_bytes(total)}")

    def _on_albums_mat_progress(self, i: int, total: int) -> None:
        self._bar_ratio(i, total)
        self.lbl_file.setText("—")
        self.lbl_counts.setText(f"{i} / {total} album files copied")

    def _on_albums_done(self, report, report_path: str) -> None:
        self.watcher.paused = False
        self._bar_done(not report.unmatched)
        self._set_ui_state(UiState.IDLE)
        self._set_report(report.to_markdown())
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
        self.error_bar.hide()
        self._set_ui_state(UiState.STABILITY)
        self.watcher.paused = True
        self._set_report(
            "# Stability check running\n\nThree full inventories will be taken. "
            "Between each pass, follow the instruction shown above the progress "
            "bar: unplug the device, then plug it back in."
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
        lines = ["# Stability check", ""]
        for r in result.rounds:
            lines.append(
                f"- Pass {r.index}: {r.count} files, {fmt_bytes(r.total_bytes)}, "
                f"fingerprint `{r.fingerprint[:20]}…` ({fmt_duration(r.duration_s)})"
            )
        lines.append("")
        lines.append("```")
        lines.append(result.verdict())
        lines.append("```")
        self._set_report("\n".join(lines))
        self.lbl_phase.setText(
            "Stability check: STABLE — three identical inventories."
            if result.stable
            else "Stability check: UNSTABLE — see the report."
        )
        if not result.stable:
            self._show_error(
                "The three inventories are not identical. Until they are, do "
                "not trust the count — see the differences in the report."
            )

    # ------------------------------------------------------------------ common
    def _on_worker_failed(self, message: str, details: str) -> None:
        self.watcher.paused = False
        self._bar_done(False)
        self._set_ui_state(UiState.IDLE)
        self.lbl_phase.setText("Failed.")
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
