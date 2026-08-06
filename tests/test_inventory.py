"""Inventory: silent truncation and disconnections must be loud."""

import pytest

from applesync.core.inventory import (
    InventoryCancelledError,
    InventoryMismatchError,
    take_inventory,
)
from applesync.device.base import DeviceDisconnectedError, DeviceLockedError
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_healthy_inventory(backend):
    with backend.connect(backend.INFO.udid) as s:
        inv = take_inventory(s)
    assert inv.count == len(backend.tree)
    assert inv.total_bytes == backend.total_bytes
    assert inv.double_checked


def test_fingerprint_is_stable(backend):
    with backend.connect(backend.INFO.udid) as s:
        a = take_inventory(s)
    with backend.connect(backend.INFO.udid) as s:
        b = take_inventory(s)
    assert a.fingerprint() == b.fingerprint()


def test_silent_truncation_is_detected():
    """THE central test: the MTP defect (files dropped without any error) is
    caught by the double enumeration and named, never absorbed."""
    faults = FaultPlan(truncate_on_walk_index=2, truncate_drop_count=30)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryMismatchError) as exc:
            take_inventory(s)
    err = exc.value
    # The files dropped in pass 2 are named, exactly 30 of them
    assert len(err.only_first) == 30
    assert len(err.only_second) == 0
    assert all("/" in p for p in err.only_first)


def test_truncation_on_the_first_pass_is_detected():
    faults = FaultPlan(truncate_on_walk_index=1, truncate_drop_count=12)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryMismatchError) as exc:
            take_inventory(s)
    assert len(exc.value.only_second) == 12


def test_disconnection_during_enumeration():
    faults = FaultPlan(disconnect_after_entries=40)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            take_inventory(s)


def test_locked_device_blocks_the_connection():
    backend = SimulatedBackend(SimProfile.small(), FaultPlan(locked=True))
    with pytest.raises(DeviceLockedError):
        backend.connect(backend.INFO.udid)


def test_cancelling_during_the_inventory(backend):
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 50

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(InventoryCancelledError):
            take_inventory(s, cancel=cancel)
