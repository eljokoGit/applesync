"""Run journal: JSONL, one event per line, flushed immediately.

Goal: being able to reconstruct any run afterwards, including one killed
mid-flight. Every line stands alone: timestamp, event kind, data. Journals
live in the destination folder (`.applesync/logs/`), next to the backup they
describe.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


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
    """Read a journal back. An unreadable line is reported, not skipped."""
    events = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                events.append({"event": "_corrupt_line", "line": i, "error": str(e)})
    return events
