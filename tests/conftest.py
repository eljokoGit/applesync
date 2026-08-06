import sys
from pathlib import Path

import pytest

# Make the package importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applesync.device.simulator import SimProfile, SimulatedBackend  # noqa: E402


@pytest.fixture
def backend() -> SimulatedBackend:
    """Healthy simulator, small profile (~180 files of a few KB)."""
    return SimulatedBackend(SimProfile.small())


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    d = tmp_path / "destination"
    d.mkdir()
    return d
