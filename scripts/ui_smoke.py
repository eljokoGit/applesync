"""Parcours UI automatisé (offscreen) avec captures d'écran.

Usage :
    python scripts/ui_smoke.py <dossier_sortie> [--real]

--real  : backend AFC réel (sans iPhone : doit afficher « Aucun iPhone détecté »)
sinon   : simulateur, parcours complet inventaire → synchro → rapport,
          puis re-inventaire (delta vide) et test de stabilité.

Chaque étape attend l'état visé avec un délai maximal : pas de « sleep et
on espère » — si l'état n'arrive pas, le script échoue bruyamment.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# Par défaut : rendu offscreen. SMOKE_PLATFORM=windows donne des captures avec
# les vraies fontes, sans jamais afficher la fenêtre (grab hors écran).
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
    raise SystemExit(f"ÉCHEC ui_smoke : condition non atteinte ({label})")


def shot(win: MainWindow, out_dir: Path, name: str) -> None:
    path = out_dir / f"{name}.png"
    win.grab().save(str(path))
    print(f"  capture : {path.name}")


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
        # Le watcher doit constater l'absence d'appareil.
        wait_until(
            app,
            lambda: "Aucun iPhone" in win.state_label.text(),
            timeout=15,
            label="bannière « Aucun iPhone détecté »",
        )
        pump(app, 0.5)
        shot(win, out_dir, "reel_01_aucun_appareil")
        assert not win.btn_inventory.isEnabled(), "Inventorier devrait être désactivé"
        print("OK (réel) : aucun appareil détecté, boutons désactivés.")
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

    print("étape 1 : appareil simulé prêt")
    wait_until(
        app,
        lambda: "prêt" in win.state_label.text(),
        timeout=15,
        label="bannière « iPhone prêt »",
    )
    shot(win, out_dir, "sim_01_pret")

    print("étape 2 : inventaire + plan")
    win._start_prepare()
    wait_until(
        app,
        lambda: win.ui_state == UiState.PLAN_PRET,
        timeout=120,
        label="plan prêt",
    )
    shot(win, out_dir, "sim_02_plan")
    assert win.prepared is not None
    n_total = win.prepared.inventory.count
    assert len(win.prepared.plan.to_copy) == n_total, "premier run : tout à copier"

    print(f"étape 3 : synchronisation de {n_total} fichiers")
    win._start_execute()
    wait_until(
        app,
        lambda: win.ui_state == UiState.REPOS,
        timeout=600,
        label="fin de synchro",
    )
    shot(win, out_dir, "sim_03_rapport")
    assert "terminée et vérifiée" in win.lbl_phase.text(), win.lbl_phase.text()

    copied = sum(1 for _ in dest.rglob("*") if _.is_file() and _.suffix in (".HEIC", ".MOV"))
    assert copied == n_total, f"{copied} fichiers sur disque, {n_total} attendus"
    parts = list(dest.rglob("*.part"))
    assert not parts, f"fichiers partiels résiduels : {parts}"

    print("étape 4 : re-inventaire (delta vide, idempotence)")
    win._start_prepare()
    wait_until(
        app,
        lambda: win.ui_state == UiState.PLAN_PRET,
        timeout=120,
        label="second plan",
    )
    assert win.prepared is not None and not win.prepared.plan.to_copy
    shot(win, out_dir, "sim_04_delta_vide")

    print("étape 5 : test de stabilité 3×")
    win._start_stability()
    wait_until(
        app,
        lambda: "stabilité" in win.lbl_phase.text().lower()
        and win.ui_state == UiState.REPOS,
        timeout=300,
        label="fin test stabilité",
    )
    shot(win, out_dir, "sim_05_stabilite")
    assert "STABLE" in win.lbl_phase.text(), win.lbl_phase.text()

    win.close()
    pump(app, 0.5)
    print("OK (simulation) : parcours complet réussi.")
    print(f"destination de démonstration : {dest}")


if __name__ == "__main__":
    main()
