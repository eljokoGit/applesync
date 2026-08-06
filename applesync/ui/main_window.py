"""Fenêtre principale AppleSync. Lisibilité de l'état avant esthétique.

Machine à états de la fenêtre :
    REPOS → PRÉPARATION → PLAN_PRÊT → SYNCHRO → REPOS
                                    ↘ interruption propre ↗
    (VÉRIFICATION et STABILITÉ sont des opérations exclusives depuis REPOS)
"""

from __future__ import annotations

import os
import threading
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtCore import QUrl
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
from applesync.core.engine import PreparedRun, ProgressSnapshot, SyncEngine
from applesync.core.layout import label_for, layout_from_id, make_layout
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
    DeviceState.ABSENT: ("#9e9e9e", "Aucun iPhone détecté",
                         "Le service Apple répond mais ne voit aucun appareil. "
                         "Branchez l'iPhone en USB ; si le câble est branché, "
                         "essayez un autre câble (certains ne font que la charge) "
                         "ou un autre port USB, et déverrouillez l'iPhone."),
    DeviceState.NO_USBMUXD: ("#c62828", "Service Apple Mobile Device injoignable",
                             "Le pilote usbmuxd ne répond pas sur 127.0.0.1:27015 : "
                             "ce PC ne peut voir aucun iPhone, même branché. "
                             "Installez iTunes (Apple Mobile Device Support) ou les "
                             "pilotes CopyTrans, puis relancez. S'il est déjà installé, "
                             "démarrez le service « Apple Mobile Device Service » "
                             "(services.msc)."),
    DeviceState.LOCKED: ("#e69100", "iPhone verrouillé",
                         "Déverrouillez l'écran de l'iPhone (code ou Face ID)."),
    DeviceState.UNTRUSTED: ("#e69100", "iPhone non appairé",
                            "Sur l'iPhone : touchez « Se fier à cet ordinateur » "
                            "puis saisissez le code."),
    DeviceState.READY: ("#1a7f37", "iPhone prêt",
                        "Vous pouvez lancer l'inventaire."),
    DeviceState.ERROR: ("#c62828", "Appareil détecté mais dialogue impossible",
                        "Débranchez/rebranchez le câble. Si cela persiste, "
                        "redémarrez le service Apple Mobile Device."),
}


class UiState(Enum):
    REPOS = auto()
    PREPARATION = auto()
    PLAN_PRET = auto()
    SYNCHRO = auto()
    VERIFICATION = auto()
    STABILITE = auto()
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
        self._expected_files: int | None = None   # compte du dernier inventaire connu
        self.current_udid: str = ""
        self.device_state: DeviceState = DeviceState.ABSENT
        self.ui_state = UiState.REPOS
        self._workers: list = []

        titre = "AppleSync — sauvegarde iPhone → PC"
        if simulate:
            titre += "   [MODE SIMULATION — aucun vrai iPhone]"
        self.setWindowTitle(titre)
        self.resize(920, 780)
        self._build()

        self.watcher = DeviceWatcher(backend)
        self.watcher.state_changed.connect(self._on_device_state)
        self.watcher.start()
        self._refresh_layout_lock()
        self._refresh_buttons()
        self._show_last_run_summary()
        self._start_update_check()

    # ------------------------------------------------------------------ maj
    def _start_update_check(self) -> None:
        """Vérifie une fois s'il existe une version plus récente.

        Désactivable : « check_updates » à false dans la configuration.
        Aucune donnée n'est envoyée, rien n'est téléchargé ni installé."""
        if not self.config.get("check_updates", True):
            return
        w = UpdateCheckWorker(__version__, self)
        w.update_available.connect(self._on_update_available)
        w.finished.connect(lambda: self._forget_worker(w))
        self._workers.append(w)
        w.start()

    def _on_update_available(self, info) -> None:
        self.update_label.setText(
            f"Version {info.latest} disponible (vous utilisez la {info.current}). "
            f"La mise à jour est manuelle : voir les notes de version."
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

        # Bannière d'état appareil
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.StyledPanel)
        banner_lay = QVBoxLayout(self.banner)
        self.state_label = QLabel("Recherche de l'appareil…")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self.state_label.setFont(f)
        self.state_hint = QLabel("")
        self.state_hint.setWordWrap(True)
        banner_lay.addWidget(self.state_label)
        banner_lay.addWidget(self.state_hint)
        root.addWidget(self.banner)

        # Destination + organisation
        dest_box = QGroupBox("Destination de la sauvegarde")
        dest_v = QVBoxLayout(dest_box)
        dest_row = QHBoxLayout()
        self.dest_label = QLabel(self._dest_text())
        self.dest_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dest_btn = QPushButton("Choisir…")
        self.dest_btn.clicked.connect(self._choose_dest)
        dest_row.addWidget(self.dest_label, stretch=1)
        dest_row.addWidget(self.dest_btn)
        dest_v.addLayout(dest_row)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Organisation :"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Miroir du DCIM (par défaut)", "miroir")
        self.layout_combo.addItem("Par date (AAAA/AAAA-MM)", "date")
        self.layout_combo.addItem(
            "Comme l'archive (AAAA/AAAA-MM, renommage date, _LivePhotos)", "archive"
        )
        self.captures_check = QCheckBox("Captures d'écran (PNG) à part")
        self.layout_lock_label = QLabel("")
        kind = self.config.get("layout", "miroir")
        idx = self.layout_combo.findData(kind)
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.captures_check.setChecked(bool(self.config.get("captures_apart", False)))
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self.captures_check.toggled.connect(self._on_layout_changed)
        layout_row.addWidget(self.layout_combo)
        layout_row.addWidget(self.captures_check)
        layout_row.addWidget(self.layout_lock_label, stretch=1)
        dest_v.addLayout(layout_row)
        root.addWidget(dest_box)

        # Actions
        actions = QHBoxLayout()
        self.btn_inventory = QPushButton("1. Inventorier")
        self.btn_sync = QPushButton("2. Synchroniser")
        self.btn_verify = QPushButton("Vérifier la destination")
        self.btn_stability = QPushButton("Test de stabilité (3×)")
        self.btn_duplicates = QPushButton("Doublons")
        self.btn_albums = QPushButton("Albums")
        self.btn_cancel = QPushButton("Interrompre proprement")
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

        # Inventaire / plan
        self.plan_box = QGroupBox("Inventaire et plan (à valider avant copie)")
        plan_lay = QVBoxLayout(self.plan_box)
        self.plan_label = QLabel("Aucun inventaire pour l'instant.")
        self.plan_label.setWordWrap(True)
        self.plan_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        plan_lay.addWidget(self.plan_label)
        root.addWidget(self.plan_box)

        # Progression
        prog_box = QGroupBox("Progression")
        grid = QGridLayout(prog_box)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.lbl_file = QLabel("—")
        self.lbl_file.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_counts = QLabel("—")
        self.lbl_speed = QLabel("—")
        self.lbl_phase = QLabel("En attente.")
        grid.addWidget(self.progress_bar, 0, 0, 1, 2)
        grid.addWidget(QLabel("Fichier :"), 1, 0)
        grid.addWidget(self.lbl_file, 1, 1)
        grid.addWidget(QLabel("Avancement :"), 2, 0)
        grid.addWidget(self.lbl_counts, 2, 1)
        grid.addWidget(QLabel("Débit / ETA :"), 3, 0)
        grid.addWidget(self.lbl_speed, 3, 1)
        grid.addWidget(QLabel("Phase :"), 4, 0)
        grid.addWidget(self.lbl_phase, 4, 1)
        grid.setColumnStretch(1, 1)
        root.addWidget(prog_box)

        # Rapport
        rep_box = QGroupBox("Rapport")
        rep_lay = QVBoxLayout(rep_box)
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        self.report_view.setPlaceholderText(
            "Le rapport final de chaque exécution s'affiche ici "
            "(et il est archivé dans <destination>/.applesync/rapports/)."
        )
        btn_row = QHBoxLayout()
        self.btn_open_reports = QPushButton("Ouvrir le dossier des rapports")
        self.btn_open_reports.clicked.connect(self._open_reports_dir)
        btn_row.addWidget(self.btn_open_reports)
        btn_row.addStretch(1)
        rep_lay.addWidget(self.report_view, stretch=1)
        rep_lay.addLayout(btn_row)
        root.addWidget(rep_box, stretch=1)

        # Bandeau de mise à jour : masqué tant qu'il n'y a rien à signaler.
        self.update_bar = QFrame()
        self.update_bar.setFrameShape(QFrame.StyledPanel)
        self.update_bar.setStyleSheet(
            "QFrame { border: 1px solid #1a7f37; border-left: 8px solid #1a7f37; "
            "border-radius: 2px; padding: 4px; }"
        )
        up_lay = QHBoxLayout(self.update_bar)
        self.update_label = QLabel("")
        self.update_label.setWordWrap(True)
        self.btn_update = QPushButton("Voir la nouvelle version")
        up_lay.addWidget(self.update_label, stretch=1)
        up_lay.addWidget(self.btn_update)
        self.update_bar.hide()
        root.addWidget(self.update_bar)

        self.setCentralWidget(central)
        self.statusBar().showMessage(f"Prêt. AppleSync {__version__}")

    # ------------------------------------------------------------------ helpers
    def _dest_text(self) -> str:
        d = self.config.destination
        return str(d) if d else "⚠ Aucune destination choisie."

    def _dest_ok(self) -> bool:
        d = self.config.destination
        return d is not None and d.exists()

    def _engine(self) -> SyncEngine:
        layout = make_layout(
            self.config.get("layout", "miroir"),
            bool(self.config.get("captures_apart", False)),
        )
        return SyncEngine(self.backend, self.config.destination, layout)

    def _on_layout_changed(self, *_):
        kind = self.layout_combo.currentData()
        self.config.set("layout", kind)
        self.config.set("captures_apart", self.captures_check.isChecked())
        self.captures_check.setEnabled(
            kind == "date" and self.layout_combo.isEnabled()
        )
        self.prepared = None
        if self.ui_state == UiState.PLAN_PRET:
            self._set_ui_state(UiState.REPOS)

    def _refresh_layout_lock(self) -> None:
        """Reflète l'organisation figée de la destination dans les options."""
        locked = None
        if self._dest_ok():
            try:
                with Manifest(self.config.destination) as m:
                    locked = m.locked_layout()
            except Exception:
                locked = None
        if locked is None:
            self.layout_combo.setEnabled(True)
            self.captures_check.setEnabled(self.layout_combo.currentData() == "date")
            self.layout_lock_label.setText("(figée à la première synchro)")
            return
        kind = "date" if locked.startswith("date") else locked
        idx = self.layout_combo.findData(kind)
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.captures_check.setChecked(locked == "date+captures")
        self.config.set("layout", kind)
        self.config.set("captures_apart", locked == "date+captures")
        self.layout_combo.setEnabled(False)
        self.captures_check.setEnabled(False)
        self.layout_lock_label.setText(
            f"FIGÉE pour cette destination : {label_for(locked)}"
        )

    def _set_ui_state(self, state: UiState) -> None:
        self.ui_state = state
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        ready = self.device_state == DeviceState.READY
        idle = self.ui_state in (UiState.REPOS, UiState.PLAN_PRET)
        busy = not idle
        self.btn_inventory.setEnabled(ready and idle and self._dest_ok())
        self.btn_sync.setEnabled(
            ready and self.ui_state == UiState.PLAN_PRET and self.prepared is not None
        )
        self.btn_verify.setEnabled(ready and idle and self._dest_ok())
        self.btn_stability.setEnabled(ready and idle)
        # Doublons : lit le manifeste local, aucun iPhone requis.
        self.btn_duplicates.setEnabled(idle and self._dest_ok())
        # Albums : option à part de la sauvegarde (iPhone + destination requis).
        self.btn_albums.setEnabled(ready and idle and self._dest_ok())
        self.btn_cancel.setEnabled(busy)
        self.dest_btn.setEnabled(idle)

    def _show_error(self, message: str, details: str = "") -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("AppleSync — échec")
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
                run_id, _, finished, inv_n, inv_b, cop_n, cop_b = last
                self._expected_files = inv_n or None
                import time as _t

                self.plan_label.setText(
                    f"Dernière synchro terminée : "
                    f"{_t.strftime('%Y-%m-%d %H:%M', _t.localtime(finished))} — "
                    f"inventaire {inv_n} fichiers ({fmt_bytes(inv_b or 0)}), "
                    f"{cop_n} copiés ({fmt_bytes(cop_b or 0)}). "
                    f"Lancez « 1. Inventorier » pour voir le delta actuel."
                )
        except Exception:
            pass  # pas de manifeste : premier usage

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
        if state != DeviceState.READY and self.ui_state == UiState.PLAN_PRET:
            # L'appareil a disparu entre l'inventaire et la validation :
            # le plan reste affiché mais la synchro se re-préparera.
            self.statusBar().showMessage(
                "Appareil plus disponible — rebranchez puis relancez l'inventaire."
            )
        self._refresh_buttons()

    # ------------------------------------------------------------------ dest
    def _choose_dest(self) -> None:
        start = str(self.config.destination or Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de sauvegarde", start
        )
        if chosen:
            self.config.destination = Path(chosen)
            self.dest_label.setText(self._dest_text())
            self.prepared = None
            self._set_ui_state(UiState.REPOS)
            self._refresh_layout_lock()
            self._show_last_run_summary()

    def _open_reports_dir(self) -> None:
        if not self._dest_ok():
            self._show_error("Aucune destination choisie.")
            return
        d = self.config.destination / ".applesync" / "rapports"
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # noqa: S606 — ouverture Explorateur voulue

    # ------------------------------------------------------------------ prepare
    def _start_prepare(self) -> None:
        if not self._dest_ok():
            self._show_error("Choisissez d'abord un dossier de destination.")
            return
        self.cancel.clear()
        self.prepared = None
        self.plan_label.setText("Inventaire en cours…")
        self._set_ui_state(UiState.PREPARATION)
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
        self.lbl_phase.setText(f"Inventaire — {phase} : {n} fichiers vus")
        self.lbl_counts.setText(f"{n} fichiers vus")
        # Barre calée sur le compte du dernier inventaire connu : chaque passe
        # de la double énumération remplit une moitié. Sinon : animation.
        attendu = self._expected_files
        if attendu:
            if "1/2" in phase:
                base, largeur = 0, 500
            elif "2/2" in phase:
                base, largeur = 500, 500
            else:
                base, largeur = 0, 1000
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(
                min(1000, base + int(largeur * min(n, attendu) / attendu))
            )
        else:
            self._bar_busy()

    def _on_prepared(self, prepared: PreparedRun) -> None:
        self.prepared = prepared
        self._expected_files = prepared.inventory.count
        self._bar_done(True)
        inv, plan = prepared.inventory, prepared.plan
        lignes = [
            f"Appareil : {prepared.device_label}",
            f"Inventaire : {inv.count} fichiers — {fmt_bytes(inv.total_bytes)} "
            f"(double énumération concordante ✓, {fmt_duration(inv.duration_s)})",
            f"Empreinte : {inv.fingerprint()[:20]}…",
            "",
            f"→ À copier : {len(plan.to_copy)} fichiers — "
            f"{fmt_bytes(sum(f.size for f in plan.to_copy))}",
            f"→ Déjà synchronisés : {len(plan.already_synced)}",
        ]
        if plan.to_adopt:
            lignes.append(
                f"→ Déjà sur disque, à ré-enregistrer (adoption) : {len(plan.to_adopt)}"
            )
        if plan.conflicts:
            lignes.append(
                f"→ ⚠ Conflits (copiés sous nom versionné, jamais d'écrasement) : "
                f"{len(plan.conflicts)}"
            )
            lignes.extend(
                f"      {c.remote.path} → {c.versioned_path}" for c in plan.conflicts[:8]
            )
            if len(plan.conflicts) > 8:
                lignes.append(f"      … et {len(plan.conflicts) - 8} autres")
        if plan.missing_on_device:
            lignes.append(
                f"→ Disparus de l'iPhone depuis la dernière synchro "
                f"(CONSERVÉS sur PC) : {len(plan.missing_on_device)}"
            )
            lignes.extend(
                f"      {e.source_path}" for e in plan.missing_on_device[:8]
            )
            if len(plan.missing_on_device) > 8:
                lignes.append(f"      … et {len(plan.missing_on_device) - 8} autres")
        if self.config.get("layout", "miroir") == "archive":
            lignes.append("")
            lignes.append(
                "Organisation archive : la date de prise de vue (EXIF) est lue "
                "pendant la copie — chaque fichier est classé et renommé "
                "d'après elle (mtime en repli), Live Photos vers _LivePhotos, "
                "doublons de contenu vers _Doublons."
            )
        if prepared.breakdown_csv is not None:
            lignes.append("")
            lignes.append(
                f"Ventilation mois × extension exportée : {prepared.breakdown_csv}"
            )
        lignes.append("")
        lignes.append("Validez en cliquant « 2. Synchroniser ».")
        self.plan_label.setText("\n".join(lignes))
        self.lbl_phase.setText("Plan prêt — en attente de validation.")
        self.statusBar().showMessage(
            "Plan prêt. Validez avec « 2. Synchroniser » — rien n'a encore été écrit."
        )
        self.watcher.paused = False
        self._set_ui_state(UiState.PLAN_PRET)

    # ------------------------------------------------------------------ execute
    def _start_execute(self) -> None:
        if self.prepared is None:
            return
        self.cancel.clear()
        self._set_ui_state(UiState.SYNCHRO)
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

    # -- barre de progression : jamais figée pendant une opération -----------
    # Convention : un changement de phase passe la barre en « activité »
    # (animation continue) tant qu'aucun compteur n'est disponible ; dès que
    # des chiffres arrivent, la barre devient un vrai pourcentage.

    def _bar_busy(self) -> None:
        self.progress_bar.setRange(0, 0)          # animation « en cours »

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
        if self.ui_state != UiState.REPOS:
            self._bar_busy()

    def _on_progress(self, s: ProgressSnapshot) -> None:
        if s.bytes_total > 0:
            self._bar_ratio(s.bytes_done, s.bytes_total)
        self.lbl_file.setText(s.current_file or "—")
        self.lbl_counts.setText(
            f"{s.files_done} / {s.files_total} fichiers — "
            f"{fmt_bytes(s.bytes_done)} / {fmt_bytes(s.bytes_total)}"
        )
        eta = s.eta_s
        self.lbl_speed.setText(
            f"{fmt_bytes(int(s.bytes_per_s))}/s"
            + (f" — ETA {fmt_duration(eta)}" if eta is not None else "")
        )

    def _on_verify_progress(self, i: int, n: int, path: str) -> None:
        """Progression d'une relecture disque (vérification, adoption)."""
        self._bar_ratio(i, n)
        self.lbl_file.setText(path or "—")
        self.lbl_counts.setText(f"{i} / {n} fichiers relus sur le disque")
        self.lbl_speed.setText("—")

    def _on_report(self, report: RunReport) -> None:
        self.report_view.setMarkdown(report.to_markdown())
        self.prepared = None
        self.watcher.paused = False
        self._bar_done(report.status == "terminé")
        self._refresh_layout_lock()   # la 1re synchro vient de figer l'organisation
        self._set_ui_state(UiState.REPOS)
        titres = {"terminé": "Synchronisation terminée et vérifiée.",
                  "interrompu": "Synchronisation interrompue — reprise possible.",
                  "échec": "SYNCHRONISATION EN ÉCHEC — lisez le rapport."}
        self.lbl_phase.setText(titres.get(report.status, report.status))
        self.statusBar().showMessage(titres.get(report.status, report.status))
        if report.status != "terminé":
            self._show_error(
                titres.get(report.status, report.status)
                + ("\n\n" + (report.error or "") if report.error else "")
            )

    # ------------------------------------------------------------------ verify
    def _start_verify(self) -> None:
        if not self._dest_ok():
            return
        self.cancel.clear()
        self._set_ui_state(UiState.VERIFICATION)
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
        self._set_ui_state(UiState.REPOS)
        lines = [
            "# Vérification de la destination",
            "",
            f"- Fichiers contrôlés : {rep.checked_count}",
            f"- Relus et hachés : {rep.hashed_count}",
            f"- Conformes : {rep.ok_count}",
        ]
        if rep.ok:
            lines.append("- **Aucun écart : la destination est fidèle à l'iPhone.**")
        else:
            lines.append(f"- **ÉCARTS : {len(rep.discrepancies)}**")
            lines.extend(
                f"  - `{d.source_path}` [{d.kind}] {d.detail}" for d in rep.discrepancies
            )
        self.report_view.setMarkdown("\n".join(lines))
        self.lbl_phase.setText(
            "Vérification : aucun écart." if rep.ok
            else f"Vérification : {len(rep.discrepancies)} ÉCART(S) — voir rapport."
        )
        if not rep.ok:
            self._show_error(
                f"{len(rep.discrepancies)} écart(s) entre l'iPhone et la destination. "
                f"Liste nominative dans le panneau Rapport. "
                f"NE SUPPRIMEZ RIEN sur l'iPhone."
            )

    # ------------------------------------------------------------------ doublons
    def _show_duplicates(self) -> None:
        """Doublons de CONTENU (SHA-256 du manifeste). Lecture locale, rapide."""
        if not self._dest_ok():
            return
        from applesync.core.duplicates import find_duplicates

        try:
            with Manifest(self.config.destination) as m:
                report = find_duplicates(m)
        except Exception as e:
            self._show_error(f"Lecture du manifeste impossible : {e}")
            return
        self.report_view.setMarkdown(report.to_markdown())
        if report.scanned_count == 0:
            self.lbl_phase.setText(
                "Doublons : manifeste vide — lancez d'abord une synchronisation."
            )
        elif report.groups:
            from applesync.core.report import fmt_bytes

            self.lbl_phase.setText(
                f"Doublons : {len(report.groups)} groupe(s), "
                f"{report.duplicate_count} exemplaire(s) excédentaire(s) "
                f"({fmt_bytes(report.wasted_bytes)}) — rien n'est supprimé."
            )
        else:
            self.lbl_phase.setText(
                f"Doublons : aucun (sur {report.scanned_count} fichiers hachés)."
            )

    # ------------------------------------------------------------------ albums
    def _start_albums(self) -> None:
        """Option à part de la sauvegarde : récupère les albums de l'iPhone
        (base Photos), matérialisés en liens vers les fichiers déjà copiés."""
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
            f"{fmt_bytes(done)} / {fmt_bytes(total)} (base Photos)"
        )

    def _on_albums_mat_progress(self, i: int, n: int) -> None:
        self._bar_ratio(i, n)
        self.lbl_file.setText("—")
        self.lbl_counts.setText(f"{i} / {n} fichiers d'albums copiés")

    def _on_albums_done(self, report, report_path: str) -> None:
        self.watcher.paused = False
        self._bar_done(not report.unmatched)
        self._set_ui_state(UiState.REPOS)
        self.report_view.setMarkdown(report.to_markdown())
        etat = (
            f"Albums : {report.albums_count} dossiers, "
            f"{report.copies_created} fichiers copiés "
            f"({fmt_bytes(report.copied_bytes)}), "
            f"{report.favorites_count} favoris"
        )
        if report.unmatched:
            etat += f" — {len(report.unmatched)} non appariés (voir rapport)"
        self.lbl_phase.setText(etat)
        self.statusBar().showMessage(f"Rapport albums : {report_path}")

    # ------------------------------------------------------------------ stability
    def _start_stability(self) -> None:
        self.cancel.clear()
        self._set_ui_state(UiState.STABILITE)
        self.watcher.paused = True
        self.report_view.setMarkdown(
            "# Test de stabilité en cours\n\nTrois inventaires complets vont être "
            "réalisés. Entre chaque passe, suivez la consigne affichée dans la "
            "barre d'état (débrancher puis rebrancher l'iPhone)."
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
        self._set_ui_state(UiState.REPOS)
        lines = ["# Test de stabilité (critère de réussite)", ""]
        for r in result.rounds:
            lines.append(
                f"- Passe {r.index} : {r.count} fichiers, {fmt_bytes(r.total_bytes)}, "
                f"empreinte `{r.fingerprint[:20]}…` ({fmt_duration(r.duration_s)})"
            )
        lines.append("")
        lines.append("```")
        lines.append(result.verdict())
        lines.append("```")
        self.report_view.setMarkdown("\n".join(lines))
        self.lbl_phase.setText(
            "Test de stabilité : STABLE ✓" if result.stable
            else "Test de stabilité : INSTABLE — voir le rapport."
        )

    # ------------------------------------------------------------------ common
    def _on_worker_failed(self, message: str, details: str) -> None:
        self.watcher.paused = False
        self._bar_done(False)
        self._set_ui_state(UiState.REPOS)
        self.lbl_phase.setText(f"Échec : {message}")
        self._show_error(message, details)

    def _request_cancel(self) -> None:
        self.cancel.set()
        self.lbl_phase.setText("Interruption demandée — arrêt propre en cours…")

    def _forget_worker(self, w) -> None:
        if w in self._workers:
            self._workers.remove(w)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.cancel.set()
        self.watcher.stop()
        self.watcher.wait(3000)
        for w in list(self._workers):
            w.wait(5000)
        shutdown = getattr(self.backend, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)
