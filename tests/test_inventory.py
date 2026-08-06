"""Inventaire : la troncature silencieuse et la déconnexion doivent être bruyantes."""

import pytest

from applesync.core.inventory import (
    InventoryCancelledError,
    InventoryMismatchError,
    take_inventory,
)
from applesync.device.base import DeviceDisconnectedError, DeviceLockedError
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_inventaire_sain(backend):
    with backend.connect(backend.INFO.udid) as s:
        inv = take_inventory(s)
    assert inv.count == len(backend.tree)
    assert inv.total_bytes == backend.total_bytes
    assert inv.double_checked


def test_empreinte_stable(backend):
    with backend.connect(backend.INFO.udid) as s:
        a = take_inventory(s)
    with backend.connect(backend.INFO.udid) as s:
        b = take_inventory(s)
    assert a.fingerprint() == b.fingerprint()


def test_troncature_silencieuse_detectee():
    """LE test central : le défaut MTP (des fichiers omis sans erreur) est
    détecté par la double énumération et nommé, jamais absorbé."""
    faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=30)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryMismatchError) as exc:
            take_inventory(s)
    err = exc.value
    # Les fichiers omis à la 2e passe sont nommés, exactement 30
    assert len(err.only_first) == 30
    assert len(err.only_second) == 0
    assert all("/" in p for p in err.only_first)


def test_troncature_premiere_passe_detectee():
    faults = FaultPlan(truncate_on_walk_index=1, truncate_drop_count=12)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryMismatchError) as exc:
            take_inventory(s)
    assert len(exc.value.only_second) == 12


def test_deconnexion_en_cours_denumeration():
    faults = FaultPlan(disconnect_after_entries=40)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            take_inventory(s)


def test_appareil_verrouille_bloque_la_connexion():
    backend = SimulatedBackend(SimProfile.small(), FaultPlan(locked=True))
    with pytest.raises(DeviceLockedError):
        backend.connect(backend.INFO.udid)


def test_annulation_pendant_inventaire(backend):
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 50

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryCancelledError):
            take_inventory(s, cancel=cancel)
