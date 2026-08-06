"""Stability check: the function that measures the success criterion."""

from applesync.core.stability import run_stability_check
from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend


def test_three_identical_passes_are_stable(backend):
    replugs = []
    result = run_stability_check(
        backend,
        backend.INFO.udid,
        rounds=3,
        wait_between_rounds=lambda i: replugs.append(i),
    )
    assert result.stable
    assert replugs == [2, 3]           # one "unplug" between each pass
    assert len(result.rounds) == 3
    assert len({r.fingerprint for r in result.rounds}) == 1
    assert "STABLE" in result.verdict()


def test_a_diverging_pass_is_caught_inside_that_pass():
    """A silently truncated enumeration during the stability check is caught
    BEFORE the inter-pass comparison: the internal double enumeration of pass
    2 already disagrees with itself."""
    import pytest

    from applesync.core.inventory import InventoryMismatchError

    # walk #3 = first enumeration of pass 2 (each pass does 2 walks)
    faults = FaultPlan(truncate_on_walk_index=3, truncate_drop_count=5)
    backend = SimulatedBackend(SimProfile.small(), faults)
    with pytest.raises(InventoryMismatchError):
        run_stability_check(
            backend, backend.INFO.udid, rounds=3, wait_between_rounds=lambda i: None
        )


def test_real_drift_between_passes_is_named(backend):
    """If the content really changes between two passes (a photo taken in the
    meantime), the verdict is UNSTABLE and names the difference."""

    def between_passes(i):
        if i == 3:
            backend.add_file("202312_a/IMG_77777.HEIC", 1234, 1_700_000_042)

    result = run_stability_check(
        backend, backend.INFO.udid, rounds=3, wait_between_rounds=between_passes
    )
    assert not result.stable
    assert any("IMG_77777" in d for d in result.diffs)
    assert "UNSTABLE" in result.verdict()
