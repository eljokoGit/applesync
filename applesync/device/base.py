"""Contrat abstrait d'accès à l'appareil.

Toute la logique métier (inventaire, plan, copie, vérification) ne voit que
ces interfaces. Règle absolue : aucune méthode d'écriture ou de suppression
côté appareil n'existe dans ce contrat — l'iPhone est en lecture seule par
construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class DeviceState(Enum):
    """État de l'appareil tel que présenté à l'UI."""

    ABSENT = "absent"            # usbmuxd répond mais aucun appareil sur le bus
    NO_USBMUXD = "no_usbmuxd"    # usbmuxd/Apple Mobile Device injoignable
    LOCKED = "locked"            # détecté mais verrouillé par code
    UNTRUSTED = "untrusted"      # détecté mais l'utilisateur n'a pas touché « Se fier »
    READY = "ready"              # appairé, session possible
    ERROR = "error"              # détecté mais dialogue impossible (cause inconnue)


@dataclass(frozen=True)
class DeviceInfo:
    udid: str
    name: str = ""
    model: str = ""
    ios_version: str = ""


@dataclass(frozen=True)
class RemoteFile:
    """Un fichier de l'appareil, tel que vu à l'inventaire.

    `path` est relatif à la racine DCIM, séparateur POSIX
    (ex. « 202301_a/IMG_0001.HEIC »).
    """

    path: str
    size: int
    mtime: int                    # epoch secondes (troncature volontaire, voir DECISIONS.md)
    birthtime: Optional[int] = None

    @property
    def identity(self) -> tuple[str, int, int]:
        """Critère d'identité incrémental : chemin + taille + mtime.

        Plus solide que le seul nom : un fichier recréé avec le même nom
        mais un contenu différent change de taille et/ou de mtime.
        """
        return (self.path, self.size, self.mtime)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Hiérarchie d'erreurs. Toute implémentation DOIT traduire ses erreurs natives
# vers ces classes : la logique métier ne connaît qu'elles.
# ---------------------------------------------------------------------------

class DeviceError(Exception):
    """Erreur d'accès appareil (base)."""


class DeviceAbsentError(DeviceError):
    """Aucun appareil connecté."""


class UsbmuxdUnavailableError(DeviceError):
    """usbmuxd (Apple Mobile Device Support / CopyTrans) ne répond pas.

    À distinguer de DeviceAbsentError : ici c'est le PC qui n'est pas prêt,
    pas l'iPhone qui manque."""


class DeviceLockedError(DeviceError):
    """Appareil verrouillé par code : déverrouiller l'écran."""


class DeviceUntrustedError(DeviceError):
    """Appairage refusé ou en attente : toucher « Se fier » sur l'iPhone."""


class DeviceDisconnectedError(DeviceError):
    """La session est tombée en cours d'opération (débranché, écran verrouillé…)."""


class FileReadError(DeviceError):
    """Échec de lecture d'un fichier, avec position exacte."""

    def __init__(self, path: str, offset: int, message: str = ""):
        self.path = path
        self.offset = offset
        super().__init__(f"Lecture échouée sur {path} à l'octet {offset}: {message}")


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

class RemoteFileReader(ABC):
    """Lecteur séquentiel d'un fichier de l'appareil, avec positionnement."""

    @abstractmethod
    def seek(self, offset: int) -> None:
        """Se positionne à `offset` octets du début."""

    @abstractmethod
    def read(self, size: int) -> bytes:
        """Lit jusqu'à `size` octets. b'' signifie fin de fichier.

        Lève FileReadError ou DeviceDisconnectedError en cas de problème —
        jamais de résultat court silencieux avant la fin du fichier.
        """

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "RemoteFileReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DeviceSession(ABC):
    """Session ouverte avec un appareil appairé.

    Une session peut tomber à tout moment (verrouillage d'écran, débranchement) :
    toutes les méthodes peuvent lever DeviceDisconnectedError.
    """

    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def walk_dcim(self) -> Iterator[RemoteFile]:
        """Énumère récursivement TOUS les fichiers sous DCIM.

        Ordre non garanti. Toute erreur interrompt l'itération par une
        exception : un générateur qui se termine sans exception a, par
        contrat, tout énuméré. (La défense contre une troncature silencieuse
        malgré ce contrat est la double énumération, voir core/inventory.py.)
        """

    @abstractmethod
    def stat(self, path: str) -> RemoteFile:
        """Stat d'un fichier par chemin relatif DCIM. Lève si absent."""

    @abstractmethod
    def open_file(self, path: str) -> RemoteFileReader: ...

    # -- accès Media hors DCIM (lecture seule), pour la base Photos.sqlite ----
    # Chemins absolus dans le jail AFC (/var/mobile/Media), ex. «/PhotoData/…».
    # Implémentation facultative : l'absence rend l'erreur, jamais un vide.

    def stat_media(self, path: str) -> int:
        """Taille d'un fichier du jail Media. Lève si absent/inaccessible."""
        raise FileReadError(path, 0, "accès Media non disponible sur ce backend")

    def open_media(self, path: str) -> RemoteFileReader:
        """Ouvre en lecture un fichier du jail Media."""
        raise FileReadError(path, 0, "accès Media non disponible sur ce backend")

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "DeviceSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DeviceBackend(ABC):
    """Point d'entrée : détection des appareils et ouverture de session."""

    @abstractmethod
    def list_devices(self) -> list[DeviceInfo]:
        """Appareils actuellement visibles sur le bus (sans ouvrir de session)."""

    @abstractmethod
    def probe_state(self, udid: str) -> DeviceState:
        """État actionnable de l'appareil (verrouillé / non appairé / prêt)."""

    @abstractmethod
    def connect(self, udid: str) -> DeviceSession:
        """Ouvre une session. Lève DeviceLockedError / DeviceUntrustedError /
        DeviceAbsentError selon l'état réel."""
