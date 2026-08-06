"""Entry point: `applesync`, or `python -m applesync [--simulate]`.

Simulation mode: a fake device (~300 files) to try the UI and demonstrate the
behaviour without hardware. Faults can be injected to SEE the application
refuse a doubtful inventory:

    applesync --simulate --sim-fault truncate
    applesync --simulate --sim-fault disconnect-walk
    applesync --simulate --sim-fault locked
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="applesync")
    parser.add_argument(
        "--simulate", action="store_true",
        help="simulated device (no hardware required)",
    )
    parser.add_argument(
        "--sim-fault",
        choices=["truncate", "disconnect-walk", "locked"],
        default=None,
        help="fault injected into the simulator (with --simulate)",
    )
    parser.add_argument(
        "--probe-albums",
        action="store_true",
        help="feasibility probe: is PhotoData/Photos.sqlite readable over AFC? "
             "(device required, strictly read-only, no UI)",
    )
    args = parser.parse_args()

    if args.probe_albums:
        return probe_albums()

    if args.sim_fault and not args.simulate:
        parser.error("--sim-fault requires --simulate")

    if args.simulate:
        from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend

        faults = FaultPlan()
        if args.sim_fault == "truncate":
            # Every second enumeration diverges -> the inventory must refuse.
            faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=7)
        elif args.sim_fault == "disconnect-walk":
            faults = FaultPlan(disconnect_after_entries=120)
        elif args.sim_fault == "locked":
            faults = FaultPlan(locked=True)
        backend = SimulatedBackend(SimProfile.demo(), faults)
    else:
        from applesync.device.afc import AfcBackend

        backend = AfcBackend()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from applesync.ui.main_window import ASSETS, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AppleSync")
    icon = ASSETS / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow(backend, simulate=args.simulate)
    win.show()
    return app.exec()


def probe_albums() -> int:
    """Album feasibility: is the Photos database readable over AFC?

    Strictly read-only: listdir + stat + reading the first 16 bytes. Prints a
    verdict; exit code 0 when the direct route is open.
    """
    from applesync.device.afc import AfcBackend

    backend = AfcBackend()
    try:
        devices = backend.list_devices()
        if not devices:
            print("VERDICT: NO DEVICE — plug in and unlock the device.")
            return 1
        session = backend.connect(devices[0].udid)
        try:
            afc, loop = session._afc, session._loop   # raw Media jail access

            print("--- AFC jail root (/var/mobile/Media) ---")
            root = loop.call(afc.listdir("/"))
            print("  " + ", ".join(sorted(root)))

            if "PhotoData" not in root:
                print("VERDICT: CLOSED — PhotoData not visible over AFC.")
                return 2

            print("--- stat /PhotoData/Photos.sqlite ---")
            st = loop.call(afc.stat("/PhotoData/Photos.sqlite"))
            size = int(st.get("st_size", 0))
            print(f"  size: {size} bytes")

            print("--- reading the first 16 bytes ---")
            handle = loop.call(afc.fopen("/PhotoData/Photos.sqlite", "r"))
            try:
                head = loop.call(afc.fread(handle, 16))
            finally:
                loop.call(afc.fclose(handle))
            expected = b"SQLite format 3\x00"
            print(f"  read: {head!r}")
            if head == expected and size > 0:
                print("VERDICT: OPEN — Photos.sqlite is readable over AFC, "
                      "album recovery works directly.")
                return 0
            print("VERDICT: DOUBTFUL — readable but unexpected header.")
            return 3
        finally:
            session.close()
    except Exception as e:
        print(f"VERDICT: CLOSED — {type(e).__name__}: {e}")
        print("(PhotoData refused over AFC on this iOS version.)")
        return 2
    finally:
        backend.shutdown()


if __name__ == "__main__":
    sys.exit(main())
