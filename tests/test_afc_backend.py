"""AFC backend without hardware: telling "usbmuxd missing" from "no device".

These tests need no iPhone: they point the backend at a dead TCP port to check
that an unreachable usbmuxd surfaces as its own actionable state, never
confused with "no device connected".
"""

import pytest

from applesync.device.base import DeviceState, UsbmuxdUnavailableError


def _backend_on_dead_port():
    from applesync.device.afc import AfcBackend

    # Port 9 (discard): nothing listens there locally.
    return AfcBackend(usbmux_address="127.0.0.1:9")


def test_unreachable_usbmuxd_raises_its_own_error():
    backend = _backend_on_dead_port()
    try:
        with pytest.raises(UsbmuxdUnavailableError):
            backend.list_devices()
    finally:
        backend.shutdown()


def test_probe_state_reports_no_usbmuxd():
    backend = _backend_on_dead_port()
    try:
        assert backend.probe_state("ANY-UDID") == DeviceState.NO_USBMUXD
    finally:
        backend.shutdown()
