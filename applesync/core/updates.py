"""Vérification de mise à jour, en consultation seule.

L'application interroge l'API GitHub pour connaître la dernière version
publiée et le signale à l'utilisateur. Elle ne télécharge rien, n'installe
rien et n'exécute rien automatiquement : pour un outil de sauvegarde, une
mise à jour silencieuse serait exactement le genre de comportement qu'on ne
veut pas. La mise à jour reste un geste explicite (voir README).

Discrétion : une seule requête HTTPS anonyme vers api.github.com, aucune
donnée envoyée, désactivable par configuration. Toute erreur (hors ligne,
API indisponible, réponse inattendue) rend None sans bruit — une
vérification de version ne doit jamais gêner une sauvegarde.
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
    """« v1.2.3 », « 1.2.3rc1 » → tuple comparable ; None si illisible.

    Une version stable l'emporte sur une pré-version de même numéro
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
    """La version publiée est-elle plus récente que celle installée ?

    Prudence : si l'une des deux est illisible, on répond False — mieux vaut
    ne rien signaler qu'annoncer une mise à jour qui n'en est pas une."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def check_for_update(current_version: str, timeout: float = 5.0) -> Optional[UpdateInfo]:
    """Rend un UpdateInfo si une version plus récente existe, sinon None.

    Ne lève jamais."""
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
