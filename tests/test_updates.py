"""Version comparison for the update check.

The logic is tested offline: `check_for_update` only wraps `is_newer` in an
HTTP call, and must return None quietly when that call fails (which the last
tests verify, with a patched urlopen)."""

import pytest

from applesync.core.updates import (
    UpdateInfo,
    check_for_update,
    is_newer,
    parse_version,
)


@pytest.mark.parametrize("text, expected", [
    ("1.0.0", (1, 0, 0, 3, 0)),
    ("v2.3.4", (2, 3, 4, 3, 0)),
    ("  1.2.3  ", (1, 2, 3, 3, 0)),
    ("1.2.3rc2", (1, 2, 3, 2, 2)),
    ("1.2.3-beta.1", (1, 2, 3, 1, 1)),
    ("", None),
    ("main", None),
    ("1.2", None),
    ("1.2.3.4.5", None),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("published, installed, newer", [
    ("1.0.1", "1.0.0", True),
    ("1.1.0", "1.0.9", True),
    ("2.0.0", "1.9.9", True),
    ("v1.0.1", "1.0.0", True),          # v prefix tolerated
    ("1.0.0", "1.0.0", False),          # identical
    ("1.0.0", "1.0.1", False),          # published one is older
    ("1.0.0", "1.0.0rc1", True),        # stable > pre-release
    ("1.0.0rc1", "1.0.0", False),
    ("1.0.0rc2", "1.0.0rc1", True),
])
def test_is_newer(published, installed, newer):
    assert is_newer(published, installed) is newer


@pytest.mark.parametrize("published, installed", [
    ("", "1.0.0"),
    ("nightly", "1.0.0"),
    ("1.0.1", "unknown"),
])
def test_unreadable_version_announces_nothing(published, installed):
    """Cautious by design: when in doubt, announce no update."""
    assert is_newer(published, installed) is False


def test_network_failure_is_silent(monkeypatch):
    """Offline or API down: None, no exception — a version check must never
    get in the way of a backup."""
    import applesync.core.updates as updates

    def boom(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert check_for_update("1.0.0") is None


def test_a_valid_response_yields_an_updateinfo(monkeypatch):
    import io

    import applesync.core.updates as updates

    payload = (b'{"tag_name": "v9.9.9", '
               b'"html_url": "https://example.invalid/r", "name": "9.9.9"}')

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(payload))
    info = check_for_update("1.0.0")
    assert isinstance(info, UpdateInfo)
    assert info.latest == "9.9.9" and info.current == "1.0.0"
    assert info.url == "https://example.invalid/r"

    # Already up to date: nothing to report
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(payload))
    assert check_for_update("9.9.9") is None
