import sys
from pathlib import Path

import pytest

# Rend le paquet importable sans installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applesync.device.simulator import FaultPlan, SimProfile, SimulatedBackend  # noqa: E402


@pytest.fixture
def backend() -> SimulatedBackend:
    """Simulateur sain, petit profil (~180 fichiers de quelques Ko)."""
    return SimulatedBackend(SimProfile.small())


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    d = tmp_path / "destination"
    d.mkdir()
    return d
