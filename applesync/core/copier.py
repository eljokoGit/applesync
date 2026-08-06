"""Moteur de copie : téléchargement vers .part, reprise exacte, SHA-256 au vol.

Garanties :
- Un fichier n'apparaît sous son nom définitif QUE complet : écriture dans
  `<cible>.part` + sidecar `.part.meta.json`, puis fsync, contrôle de taille,
  et os.replace (atomique) vers le nom final. Jamais de partiel déguisé.
- Reprise : au redémarrage, si le .part existe et que le sidecar correspond à
  l'identité source actuelle (chemin, taille, mtime), on re-hache le partiel
  local puis on continue la lecture à l'offset exact (seek côté appareil).
  Si l'identité a changé : on repart de zéro pour ce fichier.
- Le SHA-256 couvre TOUS les octets écrits (relecture du partiel comprise) :
  le hachage final est celui du fichier complet, reprise ou pas.
- Interruption utilisateur : arrêt à la frontière d'un bloc, .part conservé,
  état repris tel quel à l'exécution suivante.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from applesync.core.journal import Journal
from applesync.device.base import DeviceSession, RemoteFile

CHUNK = 1024 * 1024  # 1 Mio


class CopyError(Exception):
    """Erreur de copie non liée à l'appareil (disque plein, cible occupée…)."""


class CopyCancelled(Exception):
    """Interruption propre demandée par l'utilisateur."""


@dataclass
class CopyResult:
    remote: RemoteFile
    local_relpath: str
    sha256: str
    bytes_copied_this_run: int   # octets réellement transférés cette fois
    resumed_from: int            # 0 si copie neuve
    duration_s: float


ProgressCb = Callable[[int, int], None]   # (octets_faits_du_fichier, taille_totale)


def _sidecar_path(part_path: Path) -> Path:
    return part_path.with_name(part_path.name + ".meta.json")


def _read_sidecar(part_path: Path) -> Optional[dict]:
    sc = _sidecar_path(part_path)
    if not sc.exists():
        return None
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_sidecar(part_path: Path, remote: RemoteFile) -> None:
    sc = _sidecar_path(part_path)
    sc.write_text(
        json.dumps(
            {
                "source_path": remote.path,
                "size": remote.size,
                "mtime": remote.mtime,
            }
        ),
        encoding="utf-8",
    )


def copy_file(
    session: DeviceSession,
    remote: RemoteFile,
    dest_root: Path,
    local_relpath: str,
    journal: Journal,
    cancel: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[ProgressCb] = None,
    chunk_size: int = CHUNK,
) -> CopyResult:
    """Copie `remote` vers `dest_root/local_relpath`. Voir garanties du module."""
    start = time.time()
    dest_root = Path(dest_root)
    target = dest_root / local_relpath
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        # Le planificateur ne doit jamais nous envoyer ici ; double sécurité.
        raise CopyError(f"cible déjà occupée, copie refusée : {target}")

    # --- reprise éventuelle -------------------------------------------------
    resume_offset = 0
    hasher = hashlib.sha256()
    sidecar = _read_sidecar(part)
    if part.exists() and sidecar is not None:
        same_identity = (
            sidecar.get("source_path") == remote.path
            and sidecar.get("size") == remote.size
            and sidecar.get("mtime") == remote.mtime
        )
        part_size = part.stat().st_size
        if same_identity and 0 < part_size <= remote.size:
            # Re-hachage du partiel : le SHA final couvrira tout le fichier.
            with open(part, "rb") as fh:
                while True:
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    hasher.update(block)
            resume_offset = part_size
            journal.event(
                "reprise_fichier",
                path=remote.path,
                offset=resume_offset,
                total=remote.size,
            )
        else:
            journal.event(
                "partiel_invalide_reinitialise",
                path=remote.path,
                raison="identité source changée" if not same_identity else "taille incohérente",
            )
            part.unlink()
            _sidecar_path(part).unlink(missing_ok=True)
    elif part.exists():
        # .part orphelin sans sidecar : origine inconnue, on repart de zéro.
        journal.event("partiel_sans_sidecar_reinitialise", path=remote.path)
        part.unlink()

    _write_sidecar(part, remote)

    # --- transfert ------------------------------------------------------------
    copied_this_run = 0
    mode = "r+b" if resume_offset else "wb"
    with session.open_file(remote.path) as reader, open(part, mode) as out:
        if resume_offset:
            reader.seek(resume_offset)
            out.seek(resume_offset)
        pos = resume_offset
        while pos < remote.size:
            if cancel is not None and cancel():
                out.flush()
                os.fsync(out.fileno())
                journal.event("copie_interrompue", path=remote.path, offset=pos)
                raise CopyCancelled(remote.path)
            want = min(chunk_size, remote.size - pos)
            data = reader.read(want)
            if not data:
                # Fin de fichier avant la taille annoncée : jamais silencieux.
                raise CopyError(
                    f"{remote.path}: flux terminé à {pos} octets, "
                    f"{remote.size} attendus"
                )
            out.write(data)
            hasher.update(data)
            pos += len(data)
            copied_this_run += len(data)
            if progress_cb is not None:
                progress_cb(pos, remote.size)
        out.flush()
        os.fsync(out.fileno())

    # --- contrôles et bascule atomique ---------------------------------------
    actual = part.stat().st_size
    if actual != remote.size:
        raise CopyError(
            f"{remote.path}: taille écrite {actual} ≠ taille source {remote.size}"
        )
    sha = hasher.hexdigest()
    os.utime(part, (time.time(), remote.mtime))  # mtime local = mtime source
    os.replace(part, target)                     # atomique sur même volume
    _sidecar_path(part).unlink(missing_ok=True)

    journal.event(
        "fichier_copie",
        path=remote.path,
        local=str(local_relpath),
        taille=remote.size,
        sha256=sha,
        repris_a=resume_offset,
        duree_s=round(time.time() - start, 3),
    )
    return CopyResult(
        remote=remote,
        local_relpath=str(local_relpath),
        sha256=sha,
        bytes_copied_this_run=copied_this_run,
        resumed_from=resume_offset,
        duration_s=time.time() - start,
    )
