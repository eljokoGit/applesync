"""Update check, read-only.

The application asks the GitHub API for the latest published version and tells
the user about it. It downloads nothing, installs nothing and executes nothing
automatically: for a backup tool, a silent update is exactly the kind of
behaviour you do not want. Updating stays an explicit act (see the README).

Discretion: a single anonymous HTTPS request to api.github.com, no data sent,
disableable in the configuration. Any error (offline, API down, unexpected
response) returns None quietly — a version check must never get in the way of
a backup.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

GITHUB_REPO = "eljokoGit/applesync"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
ISSUES_PAGE = f"https://github.com/{GITHUB_REPO}/issues"

_VERSION_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|b|rc|alpha|beta)\.?(\d+)?)?$", re.I
)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2}


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    url: str
    notes: str = ""


def parse_version(text: str) -> Optional[tuple]:
    """"v1.2.3", "1.2.3rc1" -> a comparable tuple; None when unreadable.

    A stable version outranks a pre-release of the same number
    (1.2.0 > 1.2.0rc1)."""
    if not text:
        return None
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major, minor, patch, pre, pre_num = m.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 3, 0)
    return (int(major), int(minor), int(patch),
            _PRE_RANK.get(pre.lower(), 0), int(pre_num or 0))


def is_newer(latest: str, current: str) -> bool:
    """Is the published version newer than the installed one?

    Cautious by design: if either is unreadable the answer is False — better
    to stay silent than to announce an update that is not one."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def check_for_update(current_version: str, timeout: float = 5.0) -> Optional[UpdateInfo]:
    """Return an UpdateInfo when a newer version exists, otherwise None.

    Never raises."""
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"AppleSync/{current_version}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag = str(data.get("tag_name") or "")
        if not is_newer(tag, current_version):
            return None
        return UpdateInfo(
            current=current_version,
            latest=tag.lstrip("vV"),
            url=str(data.get("html_url") or RELEASES_PAGE),
            notes=str(data.get("name") or ""),
        )
    except Exception:
        return None
