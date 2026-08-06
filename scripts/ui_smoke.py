"""Automated UI walkthrough (offscreen) with screenshots.

Usage:
    python scripts/ui_smoke.py <output_dir> [--real]

--real  : the real AFC backend (without a device it must show "No device
          detected")
otherwise: the simulator, full walkthrough inventory -> sync -> report, then a
          second inventory (empty delta) and the stability check.

Every step waits for its target state with a deadline: no "sleep and hope" —
if the state never arrives, the script fails loudly.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# Offscreen rendering by default. SMOKE_PLATFORM=windows produces screenshots
# with the real fonts, without ever showing the window (offscreen grab).
os.environ.setdefault(
    "QT_QPA_PLATFORM", os.environ.get("SMOKE_PLATFORM", "offscreen")
)
SHOW_WINDOW = os.environ["QT_QPA_PLATFORM"] == "offscreen"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from applesync.core.config import Config  # noqa: E402
from applesync.ui.main_window import MainWindow, UiState  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_until(app: QApplication, predicate, timeout: float, label: str) -> None:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise SystemExit(f"ui_smoke FAILED: condition never met ({label})")


def shot(win: MainWindow, out_dir: Path, name: str) -> None:
    path = out_dir / f"{name}.png"
    win.grab().save(str(path))
    print(f"  screenshot: {path.name}")


def main() -> None:
    out_dir = Path(sys.argv[1]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    real = "--real" in sys.argv

    app = QApplication.instance() or QApplication([])

    cfg_dir = Path(tempfile.mkdtemp(prefix="applesync-smoke-cfg-"))
    config = Config(path=cfg_dir / "config.json")

    if real:
        from applesync.device.afc import AfcBackend

        backend = AfcBackend()
        win = MainWindow(backend, simulate=False, config=config)
        if SHOW_WINDOW:
            win.show()
        # The watcher must report the absence of a device.
        wait_until(
            app,
            lambda: "No device" in win.state_label.text(),
            timeout=15,
            label='"No device detected" banner',
        )
        pump(app, 0.5)
        shot(win, out_dir, "real_01_no_device")
        assert not win.btn_inventory.isEnabled(), "Inventory should be disabled"
        print("OK (real): no device detected, buttons disabled.")
        win.close()
        pump(app, 0.5)
        return

    from applesync.device.simulator import SimProfile, SimulatedBackend

    backend = SimulatedBackend(SimProfile.demo())
    dest = Path(tempfile.mkdtemp(prefix="applesync-smoke-dest-"))
    config.destination = dest

    win = MainWindow(backend, simulate=True, config=config)
    if SHOW_WINDOW:
        win.show()

    print("step 1: simulated device ready")
    wait_until(
        app,
        lambda: "ready" in win.state_label.text().lower(),
        timeout=15,
        label='"Device ready" banner',
    )
    shot(win, out_dir, "sim_01_ready")

    print("step 2: inventory + plan")
    win._start_prepare()
    wait_until(
        app,
        lambda: win.ui_state == UiState.PLAN_READY,
        timeout=120,
        label="plan ready",
    )
    shot(win, out_dir, "sim_02_plan")
    assert win.prepared is not None
    n_total = win.prepared.inventory.count
    assert len(win.prepared.plan.to_copy) == n_total, "first run: everything to copy"

    print(f"step 3: synchronising {n_total} files")
    win._start_execute()
    wait_until(
        app,
        lambda: win.ui_state == UiState.IDLE,
        timeout=600,
        label="end of sync",
    )
    shot(win, out_dir, "sim_03_report")
    assert "completed and verified" in win.lbl_phase.text(), win.lbl_phase.text()

    copied = sum(
        1 for p in dest.rglob("*") if p.is_file() and p.suffix in (".HEIC", ".MOV")
    )
    assert copied == n_total, f"{copied} files on disk, {n_total} expected"
    parts = list(dest.rglob("*.part"))
    assert not parts, f"leftover partial files: {parts}"

    print("step 4: second inventory (empty delta, idempotence)")
    win._start_prepare()
    wait_until(
        app,
        lambda: win.ui_state == UiState.PLAN_READY,
        timeout=120,
        label="second plan",
    )
    assert win.prepared is not None and not win.prepared.plan.to_copy
    shot(win, out_dir, "sim_04_empty_delta")

    print("step 5: stability check 3x")
    win._start_stability()
    wait_until(
        app,
        lambda: "stability" in win.lbl_phase.text().lower()
        and win.ui_state == UiState.IDLE,
        timeout=300,
        label="end of stability check",
    )
    shot(win, out_dir, "sim_05_stability")
    assert "STABLE" in win.lbl_phase.text(), win.lbl_phase.text()

    win.close()
    pump(app, 0.5)
    print("OK (simulation): full walkthrough succeeded.")
    print(f"demo destination: {dest}")


if __name__ == "__main__":
    main()
