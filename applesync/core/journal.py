"""Journal d'exécution : JSONL, un événement par ligne, flush immédiat.

Objectif : pouvoir reconstituer chaque exécution après coup, y compris une
exécution tuée en plein vol. Chaque ligne est autonome : horodatage, type
d'événement, données. Les journaux vivent dans le dossier de destination
(`.applesync/logs/`), avec la sauvegarde qu'ils décrivent.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


class Journal:
    LOGS_RELPATH = Path(".applesync") / "logs"

    def __init__(self, dest_root: Path, run_id: str):
        self.run_id = run_id
        self.dir = Path(dest_root) / self.LOGS_RELPATH
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"run_{run_id}.jsonl"
        self._fh = open(self.path, "a", encoding="utf-8", buffering=1)

    def event(self, kind: str, **data: Any) -> None:
        record = {"ts": round(time.time(), 3), "run": self.run_id, "event": kind}
        record.update(data)
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_journal(path: Path) -> list[dict]:
    """Relit un journal. Une ligne illisible est signalée, pas ignorée."""
    events = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                events.append({"event": "_ligne_corrompue", "ligne": i, "erreur": str(e)})
    return events
