"""Point d'entrée : `python -m applesync` (réel) ou `python -m applesync --simulate`.

Mode simulation : iPhone factice (~300 fichiers, ~250 Mo) pour prendre en main
l'UI et démontrer le comportement sans appareil. Des pannes peuvent être
injectées pour VOIR l'application refuser un inventaire douteux :

    python -m applesync --simulate --sim-fault truncate
    python -m applesync --simulate --sim-fault disconnect-walk
    python -m applesync --simulate --sim-fault locked
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="applesync")
    parser.add_argument(
        "--simulate", action="store_true",
        help="iPhone simulé (aucun appareil requis)",
    )
    parser.add_argument(
        "--sim-fault",
        choices=["truncate", "disconnect-walk", "locked"],
        default=None,
        help="panne injectée dans le simulateur (avec --simulate)",
    )
    parser.add_argument(
        "--probe-albums",
        action="store_true",
        help="sonde de faisabilité : PhotoData/Photos.sqlite est-il lisible "
             "par AFC ? (iPhone branché requis, lecture seule, sans UI)",
    )
    args = parser.parse_args()

    if args.probe_albums:
        return probe_albums()

    if args.sim_fault and not args.simulate:
        parser.error("--sim-fault nécessite --simulate")

    if args.simulate:
        from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend

        faults = FaultPlan()
        if args.sim_fault == "truncate":
            # Toutes les 2e énumérations divergent → l'inventaire doit refuser.
            faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=7)
        elif args.sim_fault == "disconnect-walk":
            faults = FaultPlan(disconnect_after_entries=120)
        elif args.sim_fault == "locked":
            faults = FaultPlan(locked=True)
        backend = SimulatedBackend(SimProfile.demo(), faults)
    else:
        from applesync.device.afc import AfcBackend

        backend = AfcBackend()

    from PySide6.QtWidgets import QApplication

    from applesync.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow(backend, simulate=args.simulate)
    win.show()
    return app.exec()


def probe_albums() -> int:
    """Faisabilité « albums » : la base Photos.sqlite est-elle lisible par AFC ?

    Lecture seule stricte : listdir + stat + lecture des 16 premiers octets.
    Verdict imprimé, code 0 si la route directe est ouverte.
    """
    from applesync.device.afc import AfcBackend

    backend = AfcBackend()
    try:
        devices = backend.list_devices()
        if not devices:
            print("VERDICT: PAS D'IPHONE — branchez et déverrouillez l'appareil.")
            return 1
        session = backend.connect(devices[0].udid)
        try:
            afc, loop = session._afc, session._loop   # accès brut au jail Media

            print("--- racine du jail AFC (/var/mobile/Media) ---")
            racine = loop.call(afc.listdir("/"))
            print("  " + ", ".join(sorted(racine)))

            if "PhotoData" not in racine:
                print("VERDICT: FERMÉ — PhotoData invisible par AFC (route backup seulement).")
                return 2

            print("--- stat /PhotoData/Photos.sqlite ---")
            st = loop.call(afc.stat("/PhotoData/Photos.sqlite"))
            taille = int(st.get("st_size", 0))
            print(f"  taille : {taille} octets")

            print("--- lecture des 16 premiers octets ---")
            handle = loop.call(afc.fopen("/PhotoData/Photos.sqlite", "r"))
            try:
                tete = loop.call(afc.fread(handle, 16))
            finally:
                loop.call(afc.fclose(handle))
            attendu = b"SQLite format 3\x00"
            print(f"  lu : {tete!r}")
            if tete == attendu and taille > 0:
                print("VERDICT: OUVERT — Photos.sqlite lisible par AFC, "
                      "la récupération des albums est faisable en direct.")
                return 0
            print("VERDICT: DOUTEUX — lisible mais en-tête inattendu, à analyser.")
            return 3
        finally:
            session.close()
    except Exception as e:
        print(f"VERDICT: FERMÉ — {type(e).__name__}: {e}")
        print("(PhotoData refusé par AFC : il resterait la route backup complet.)")
        return 2
    finally:
        backend.shutdown()


if __name__ == "__main__":
    sys.exit(main())
