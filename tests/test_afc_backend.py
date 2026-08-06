"""Backend AFC sans appareil : distinction « usbmuxd absent » / « iPhone absent ».

Ces tests n'exigent aucun iPhone : ils pointent le backend vers un port TCP
mort pour vérifier que l'indisponibilité d'usbmuxd remonte comme un état
distinct et actionnable, jamais confondu avec « aucun appareil ».
"""

import pytest

from applesync.device.base import DeviceState, UsbmuxdUnavailableError


def _backend_port_mort():
    from applesync.device.afc import AfcBackend

    # Port 9 (discard) : rien n'écoute dessus en local.
    return AfcBackend(usbmux_address="127.0.0.1:9")


def test_usbmuxd_injoignable_est_une_erreur_dediee():
    backend = _backend_port_mort()
    try:
        with pytest.raises(UsbmuxdUnavailableError):
            backend.list_devices()
    finally:
        backend.shutdown()


def test_probe_state_rend_no_usbmuxd():
    backend = _backend_port_mort()
    try:
        assert backend.probe_state("UDID-QUELCONQUE") == DeviceState.NO_USBMUXD
    finally:
        backend.shutdown()
