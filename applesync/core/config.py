"""Configuration locale : destination mémorisée, préférences UI.

Vit dans %LOCALAPPDATA%/AppleSync/config.json (hors dépôt, hors destination).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    return Path(base) / "AppleSync"


class Config:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (config_dir() / "config.json")
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                # Config illisible : on repart proprement mais on garde une trace.
                corrupt = self.path.with_suffix(".corrompu.json")
                try:
                    self.path.replace(corrupt)
                except OSError:
                    pass
                self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    @property
    def destination(self) -> Optional[Path]:
        d = self.get("destination")
        return Path(d) if d else None

    @destination.setter
    def destination(self, value: Path) -> None:
        self.set("destination", str(value))
