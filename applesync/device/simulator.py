"""Backend simulé : DCIM réaliste, déterministe, avec injection de pannes.

Le contenu de chaque fichier est dérivé de sha256(seed + chemin) et généré en
flux : on peut simuler 109 Go sans rien stocker. Même seed → même arbre, mêmes
octets, mêmes hachages. Toute la logique métier est validée là-dessus.

Pannes injectables (les pannes réelles du terrain) :
- appareil verrouillé / non appairé à la connexion ;
- déconnexion en cours d'énumération ;
- énumération tronquée SANS erreur (le défaut MTP observé) ;
- lecture échouant à mi-fichier ;
- déconnexion à mi-lecture.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from .base import (
    DeviceAbsentError,
    DeviceBackend,
    DeviceDisconnectedError,
    DeviceInfo,
    DeviceLockedError,
    DeviceSession,
    DeviceState,
    DeviceUntrustedError,
    FileReadError,
    RemoteFile,
    RemoteFileReader,
)

_CHUNK = 65536


# ---------------------------------------------------------------------------
# Profils : forme de l'arbre DCIM simulé
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimProfile:
    """Paramètres de génération de l'arbre DCIM."""

    seed: int = 20260804
    first_month: tuple[int, int] = (2017, 1)     # (année, mois)
    last_month: tuple[int, int] = (2026, 8)
    files_per_folder_range: tuple[int, int] = (250, 450)
    heic_size_range: tuple[int, int] = (1_200_000, 3_200_000)
    mov_size_range: tuple[int, int] = (2_000_000, 15_000_000)
    mov_ratio: float = 0.10                       # proportion de vidéos

    @staticmethod
    def realistic() -> "SimProfile":
        """~40 000 fichiers, ~109 Go — métadonnées seulement en pratique."""
        return SimProfile()

    @staticmethod
    def small(seed: int = 42) -> "SimProfile":
        """Petit profil pour les tests : ~180 fichiers de quelques Ko."""
        return SimProfile(
            seed=seed,
            first_month=(2023, 1),
            last_month=(2023, 12),
            files_per_folder_range=(12, 18),
            heic_size_range=(2_000, 20_000),
            mov_size_range=(30_000, 120_000),
        )

    @staticmethod
    def demo(seed: int = 20240101) -> "SimProfile":
        """Profil de démonstration UI : ~300 fichiers, ~250 Mo — assez gros
        pour voir progression/débit/ETA, assez petit pour un essai rapide."""
        return SimProfile(
            seed=seed,
            first_month=(2024, 1),
            last_month=(2025, 6),
            files_per_folder_range=(12, 20),
            heic_size_range=(300_000, 1_200_000),
            mov_size_range=(2_000_000, 8_000_000),
        )


def _months(profile: SimProfile) -> List[str]:
    (y, m), (ly, lm) = profile.first_month, profile.last_month
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}{m:02d}_a")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def build_tree(profile: SimProfile) -> List[RemoteFile]:
    """Génère l'arbre DCIM complet, trié par chemin. Déterministe."""
    rng = random.Random(profile.seed)
    files: List[RemoteFile] = []
    counter = 1
    for folder in _months(profile):
        year, month = int(folder[:4]), int(folder[4:6])
        month_epoch = int(
            (year - 1970) * 365.25 * 86400 + (month - 1) * 30.4 * 86400
        )
        n = rng.randint(*profile.files_per_folder_range)
        for _ in range(n):
            is_mov = rng.random() < profile.mov_ratio
            ext = "MOV" if is_mov else "HEIC"
            size = rng.randint(
                *(profile.mov_size_range if is_mov else profile.heic_size_range)
            )
            name = f"IMG_{counter:05d}.{ext}"
            mtime = month_epoch + rng.randint(0, 29 * 86400)
            files.append(
                RemoteFile(
                    path=f"{folder}/{name}",
                    size=size,
                    mtime=mtime,
                    birthtime=mtime - rng.randint(0, 3600),
                )
            )
            counter += 1
    return files


def content_stream(seed: int, path: str, size: int, offset: int = 0) -> Iterator[bytes]:
    """Octets déterministes du fichier simulé, à partir de `offset`.

    Le flux est fonction de (seed, path) uniquement : reproductible,
    adressable à n'importe quel offset (nécessaire pour tester la reprise).
    """
    key = hashlib.sha256(f"{seed}:{path}".encode()).digest()
    block_index = offset // 32
    skip = offset % 32
    produced = offset
    while produced < size:
        block = hashlib.sha256(key + block_index.to_bytes(8, "big")).digest()
        if skip:
            block = block[skip:]
            skip = 0
        take = min(len(block), size - produced)
        yield block[:take]
        produced += take
        block_index += 1


def content_sha256(seed: int, path: str, size: int) -> str:
    """SHA-256 attendu du fichier simulé (référence pour les tests)."""
    h = hashlib.sha256()
    for chunk in content_stream(seed, path, size):
        h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Plan de pannes
# ---------------------------------------------------------------------------

@dataclass
class FaultPlan:
    """Pannes à injecter. Tous les champs sont optionnels (défaut : aucune panne).

    Les compteurs `walk_calls` / `read_calls` permettent de cibler la N-ième
    opération (ex. : tronquer seulement la 2e énumération).
    """

    locked: bool = False                      # connect() → DeviceLockedError
    untrusted: bool = False                   # connect() → DeviceUntrustedError
    absent: bool = False                      # connect() → DeviceAbsentError

    # Déconnexion après N entrées énumérées (sur l'énumération n° walk_index, 1-based ; 0 = toutes)
    disconnect_after_entries: Optional[int] = None
    disconnect_on_walk_index: int = 0

    # Troncature SILENCIEUSE : l'énumération n° `truncate_on_walk_index` (1-based)
    # omet `truncate_drop_count` fichiers, sans lever d'erreur. Le défaut MTP.
    truncate_on_walk_index: Optional[int] = None
    truncate_drop_count: int = 50

    # Lecture : échec à l'octet N du fichier `fail_read_path`
    fail_read_path: Optional[str] = None
    fail_read_at_byte: int = 0
    fail_read_as_disconnect: bool = False     # True → DeviceDisconnectedError (session tombe)

    # État interne (compteurs d'appels)
    walk_calls: int = field(default=0, init=False)


# ---------------------------------------------------------------------------
# Implémentation
# ---------------------------------------------------------------------------

class _SimReader(RemoteFileReader):
    def __init__(self, session: "SimulatedSession", f: RemoteFile):
        self._session = session
        self._file = f
        self._pos = 0
        self._closed = False

    def seek(self, offset: int) -> None:
        self._session._check_alive()
        if offset < 0 or offset > self._file.size:
            raise FileReadError(self._file.path, offset, "seek hors bornes")
        self._pos = offset

    def read(self, size: int) -> bytes:
        self._session._check_alive()
        if self._closed:
            raise FileReadError(self._file.path, self._pos, "lecteur fermé")
        faults = self._session._faults
        end = min(self._pos + size, self._file.size)

        # Panne de lecture ciblée sur ce fichier : on livre les octets jusqu'au
        # point de panne, puis la lecture suivante échoue.
        if faults.fail_read_path == self._file.path:
            fail_at = faults.fail_read_at_byte
            if self._pos >= fail_at:
                if faults.fail_read_as_disconnect:
                    self._session._drop()
                    raise DeviceDisconnectedError(
                        f"session coupée pendant la lecture de {self._file.path}"
                    )
                raise FileReadError(self._file.path, self._pos, "panne injectée")
            if fail_at < end:
                end = fail_at

        if self._pos >= self._file.size:
            return b""
        backend = self._session._backend
        key = backend.content_key(self._file.path)
        if key in backend.content_bytes:
            data = backend.content_bytes[key][self._pos:end]
        else:
            data = b"".join(
                content_stream(self._session._profile.seed, key, end, self._pos)
            )
        self._pos = end
        return data

    def close(self) -> None:
        self._closed = True


class _BytesReader(RemoteFileReader):
    """Lecteur sur octets en mémoire (fichiers Media simulés)."""

    def __init__(self, path: str, data: Optional[bytes]):
        if data is None:
            raise FileReadError(path, 0, "fichier Media absent (simulé)")
        self._path = path
        self._data = data
        self._pos = 0

    def seek(self, offset: int) -> None:
        if offset < 0 or offset > len(self._data):
            raise FileReadError(self._path, offset, "seek hors bornes")
        self._pos = offset

    def read(self, size: int) -> bytes:
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class SimulatedSession(DeviceSession):
    def __init__(self, backend: "SimulatedBackend"):
        self._backend = backend
        self._profile = backend.profile
        self._faults = backend.faults
        self._alive = True

    def _check_alive(self) -> None:
        if not self._alive:
            raise DeviceDisconnectedError("session déjà tombée")

    def _drop(self) -> None:
        self._alive = False

    def device_info(self) -> DeviceInfo:
        self._check_alive()
        return self._backend.INFO

    def walk_dcim(self) -> Iterator[RemoteFile]:
        self._check_alive()
        self._faults.walk_calls += 1
        walk_no = self._faults.walk_calls
        tree = self._backend.tree

        drop: set[int] = set()
        if self._faults.truncate_on_walk_index == walk_no:
            # Troncature déterministe : on omet des fichiers répartis dans l'arbre.
            rng = random.Random(self._profile.seed ^ walk_no)
            k = min(self._faults.truncate_drop_count, len(tree))
            drop = set(rng.sample(range(len(tree)), k))

        disconnect_at = None
        if self._faults.disconnect_after_entries is not None and (
            self._faults.disconnect_on_walk_index in (0, walk_no)
        ):
            disconnect_at = self._faults.disconnect_after_entries

        yielded = 0
        for i, f in enumerate(tree):
            self._check_alive()
            if disconnect_at is not None and yielded >= disconnect_at:
                self._drop()
                raise DeviceDisconnectedError(
                    f"appareil déconnecté après {yielded} entrées"
                )
            if i in drop:
                continue  # silencieux : c'est le défaut MTP simulé
            yielded += 1
            yield f

    def stat(self, path: str) -> RemoteFile:
        self._check_alive()
        f = self._backend.by_path.get(path)
        if f is None:
            raise FileReadError(path, 0, "fichier absent")
        return f

    def open_file(self, path: str) -> RemoteFileReader:
        self._check_alive()
        return _SimReader(self, self.stat(path))

    # -- jail Media simulé (tests albums) ------------------------------------

    def stat_media(self, path: str) -> int:
        self._check_alive()
        data = self._backend.media_files.get(path)
        if data is None:
            raise FileReadError(path, 0, "fichier Media absent (simulé)")
        return len(data)

    def open_media(self, path: str) -> RemoteFileReader:
        self._check_alive()
        return _BytesReader(path, self._backend.media_files.get(path))

    def close(self) -> None:
        self._alive = False


class SimulatedBackend(DeviceBackend):
    INFO = DeviceInfo(
        udid="SIMULATEUR-0000",
        name="iPhone simulé",
        model="iPhone-Sim",
        ios_version="17.0-sim",
    )

    def __init__(self, profile: SimProfile | None = None, faults: FaultPlan | None = None):
        self.profile = profile or SimProfile.small()
        self.faults = faults or FaultPlan()
        self.tree: List[RemoteFile] = build_tree(self.profile)
        self.by_path: Dict[str, RemoteFile] = {f.path: f for f in self.tree}
        # Alias de contenu : chemin → chemin dont il partage les octets
        # (sert à simuler des doublons de contenu réels).
        self.content_alias: Dict[str, str] = {}
        # Contenus explicites : chemin → octets réels (sert aux tests qui ont
        # besoin de vrais formats, ex. JPEG avec EXIF).
        self.content_bytes: Dict[str, bytes] = {}
        # Jail Media simulé hors DCIM (ex. /PhotoData/Photos.sqlite pour les
        # tests de récupération d'albums).
        self.media_files: Dict[str, bytes] = {}

    def content_key(self, path: str) -> str:
        return self.content_alias.get(path, path)

    def add_file_with_content(self, path: str, data: bytes, mtime: int) -> RemoteFile:
        """Ajoute un fichier dont les octets sont fournis tels quels."""
        f = self.add_file(path, len(data), mtime)
        self.content_bytes[path] = data
        return f

    # -- mutations de l'arbre pour les tests d'incrémental ------------------

    def add_file(self, path: str, size: int, mtime: int) -> RemoteFile:
        f = RemoteFile(path=path, size=size, mtime=mtime, birthtime=mtime)
        self.tree.append(f)
        self.tree.sort(key=lambda x: x.path)
        self.by_path[path] = f
        return f

    def remove_file(self, path: str) -> None:
        """Simule une suppression faite PAR L'UTILISATEUR sur son iPhone.

        (L'application, elle, n'a aucun moyen d'écrire sur l'appareil :
        le contrat DeviceSession est en lecture seule.)
        """
        self.tree = [f for f in self.tree if f.path != path]
        del self.by_path[path]

    def replace_file(self, path: str, new_size: int, new_mtime: int) -> RemoteFile:
        self.remove_file(path)
        return self.add_file(path, new_size, new_mtime)

    def clone_file(self, src_path: str, new_path: str, mtime: int) -> RemoteFile:
        """Ajoute un fichier au CONTENU identique à `src_path` (vrai doublon)."""
        src = self.by_path[src_path]
        clone = self.add_file(new_path, src.size, mtime)
        self.content_alias[new_path] = self.content_key(src_path)
        return clone

    # -- contrat DeviceBackend ----------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        if self.faults.absent:
            return []
        return [self.INFO]

    def probe_state(self, udid: str) -> DeviceState:
        if self.faults.absent:
            return DeviceState.ABSENT
        if self.faults.locked:
            return DeviceState.LOCKED
        if self.faults.untrusted:
            return DeviceState.UNTRUSTED
        return DeviceState.READY

    def connect(self, udid: str) -> DeviceSession:
        if self.faults.absent:
            raise DeviceAbsentError("aucun appareil")
        if self.faults.locked:
            raise DeviceLockedError("appareil verrouillé — déverrouillez l'écran")
        if self.faults.untrusted:
            raise DeviceUntrustedError("touchez « Se fier » sur l'iPhone")
        return SimulatedSession(self)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.tree)
