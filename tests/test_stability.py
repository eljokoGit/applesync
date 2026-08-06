"""Test de stabilité : la fonction qui mesure le critère de réussite."""

from applesync.core.stability import run_stability_check
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_trois_passes_identiques_stable(backend):
    reconnects = []
    result = run_stability_check(
        backend,
        backend.INFO.udid,
        rounds=3,
        wait_between_rounds=lambda i: reconnects.append(i),
    )
    assert result.stable
    assert reconnects == [2, 3]           # un « débranchement » entre chaque passe
    assert len(result.rounds) == 3
    assert len({r.fingerprint for r in result.rounds}) == 1
    assert "STABLE" in result.verdict()


def test_passe_divergente_rend_instable_et_nomme():
    """Une énumération tronquée silencieusement au milieu du test de stabilité
    doit être attrapée AVANT même la comparaison inter-passes : la double
    énumération interne de la passe 2 diverge."""
    import pytest

    from applesync.core.inventory import InventoryMismatchError

    # walk n°3 = 1re énumération de la passe 2 (chaque passe fait 2 walks)
    faults = FaultPlan(truncate_on_walk_index=3, truncate_drop_count=5)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with pytest.raises(InventoryMismatchError):
        run_stability_check(
            backend, backend.INFO.udid, rounds=3, wait_between_rounds=lambda i: None
        )


def test_vraie_derive_entre_passes_nommee(backend):
    """Si le contenu change réellement entre deux passes (photo prise entre
    temps), le verdict est INSTABLE avec l'écart nominatif."""

    def entre_passes(i):
        if i == 3:
            backend.add_file("202312_a/IMG_77777.HEIC", 1234, 1_700_000_042)

    result = run_stability_check(
        backend, backend.INFO.udid, rounds=3, wait_between_rounds=entre_passes
    )
    assert not result.stable
    assert any("IMG_77777" in d for d in result.diffs)
    assert "INSTABLE" in result.verdict()
