"""Manifeste : base SQLite des fichiers déjà rapatriés.

Vit DANS le dossier de destination (`.applesync/manifest.sqlite3`) : la
sauvegarde est autoportante, le manifeste voyage avec elle.

Identité d'un fichier source : (source_path, size, mtime) — voir DECISIONS.md.
Chaque entrée porte aussi le SHA-256 calculé pendant la copie : c'est la
référence pour la vérification de la destination.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from applesync.device.base import RemoteFile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,
    birthtime INTEGER,
    sha256 TEXT NOT NULL,
    local_path TEXT NOT NULL,
    synced_at REAL NOT NULL,
    run_id TEXT NOT NULL,
    device_udid TEXT NOT NULL,
    UNIQUE (source_path, size, mtime)
);
CREATE INDEX IF NOT EXISTS idx_files_source_path ON files (source_path);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files (sha256);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    device_udid TEXT,
    status TEXT NOT NULL,              -- running | completed | interrupted | failed
    inventory_count INTEGER,
    inventory_bytes INTEGER,
    copied_count INTEGER DEFAULT 0,
    copied_bytes INTEGER DEFAULT 0,
    report_path TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ManifestEntry:
    source_path: str
    size: int
    mtime: int
    birthtime: Optional[int]
    sha256: str
    local_path: str
    synced_at: float
    run_id: str
    device_udid: str

    @property
    def identity(self) -> tuple[str, int, int]:
        return (self.source_path, self.size, self.mtime)


class Manifest:
    """Accès au manifeste. Une connexion par instance, WAL, écritures durables."""

    DB_RELPATH = Path(".applesync") / "manifest.sqlite3"

    def __init__(self, dest_root: Path):
        self.dest_root = Path(dest_root)
        db_path = self.dest_root / self.DB_RELPATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- fichiers ------------------------------------------------------------

    def record_file(
        self,
        f: RemoteFile,
        sha256: str,
        local_path: str,
        run_id: str,
        device_udid: str,
    ) -> None:
        """Enregistre un fichier copié ET vérifié. Commit immédiat : si le
        processus meurt juste après, le manifeste reste cohérent."""
        self._con.execute(
            "INSERT OR REPLACE INTO files "
            "(source_path, size, mtime, birthtime, sha256, local_path,"
            " synced_at, run_id, device_udid) VALUES (?,?,?,?,?,?,?,?,?)",
            (f.path, f.size, f.mtime, f.birthtime, sha256, local_path,
             time.time(), run_id, device_udid),
        )
        self._con.commit()

    def lookup(self, identity: tuple[str, int, int]) -> Optional[ManifestEntry]:
        row = self._con.execute(
            "SELECT source_path, size, mtime, birthtime, sha256, local_path,"
            " synced_at, run_id, device_udid FROM files"
            " WHERE source_path=? AND size=? AND mtime=?",
            identity,
        ).fetchone()
        return ManifestEntry(*row) if row else None

    def lookup_by_content(self, sha256: str, size: int) -> Optional[ManifestEntry]:
        """Première entrée au même contenu (hachage + taille), ou None.

        Sert au rangement des doublons pendant la copie : si le contenu
        fraîchement copié existe déjà quelque part, le nouvel exemplaire est
        un doublon."""
        row = self._con.execute(
            "SELECT source_path, size, mtime, birthtime, sha256, local_path,"
            " synced_at, run_id, device_udid FROM files"
            " WHERE sha256=? AND size=? ORDER BY synced_at LIMIT 1",
            (sha256, size),
        ).fetchone()
        return ManifestEntry(*row) if row else None

    def entries_for_path(self, source_path: str) -> list[ManifestEntry]:
        rows = self._con.execute(
            "SELECT source_path, size, mtime, birthtime, sha256, local_path,"
            " synced_at, run_id, device_udid FROM files WHERE source_path=?",
            (source_path,),
        ).fetchall()
        return [ManifestEntry(*r) for r in rows]

    def all_entries(self) -> list[ManifestEntry]:
        rows = self._con.execute(
            "SELECT source_path, size, mtime, birthtime, sha256, local_path,"
            " synced_at, run_id, device_udid FROM files ORDER BY source_path"
        ).fetchall()
        return [ManifestEntry(*r) for r in rows]

    def local_paths_in_use(self) -> set[str]:
        rows = self._con.execute("SELECT local_path FROM files").fetchall()
        return {r[0] for r in rows}

    # -- métadonnées de la destination ----------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        row = self._con.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value)
        )
        self._con.commit()

    def locked_layout(self) -> Optional[str]:
        """Organisation figée de cette destination, ou None si vierge.

        Un manifeste peuplé antérieur à l'option d'organisation est,
        par construction, en disposition miroir."""
        locked = self.get_meta("layout")
        if locked is None and self._con.execute(
            "SELECT 1 FROM files LIMIT 1"
        ).fetchone():
            return "miroir"
        return locked

    # -- exécutions ------------------------------------------------------------

    def start_run(self, run_id: str, device_udid: str) -> None:
        self._con.execute(
            "INSERT INTO runs (run_id, started_at, device_udid, status)"
            " VALUES (?,?,?,'running')",
            (run_id, time.time(), device_udid),
        )
        self._con.commit()

    def update_run(self, run_id: str, **cols) -> None:
        allowed = {
            "finished_at", "status", "inventory_count", "inventory_bytes",
            "copied_count", "copied_bytes", "report_path",
        }
        bad = set(cols) - allowed
        if bad:
            raise ValueError(f"colonnes inconnues : {bad}")
        sets = ", ".join(f"{k}=?" for k in cols)
        self._con.execute(
            f"UPDATE runs SET {sets} WHERE run_id=?",
            (*cols.values(), run_id),
        )
        self._con.commit()

    def last_completed_run(self) -> Optional[tuple]:
        return self._con.execute(
            "SELECT run_id, started_at, finished_at, inventory_count,"
            " inventory_bytes, copied_count, copied_bytes FROM runs"
            " WHERE status='completed' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
