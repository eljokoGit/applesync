"""Stratégies d'organisation de la destination.

- « miroir » (défaut) : l'arborescence DCIM telle quelle.
- « date » : AAAA/AAAA-MM/ d'après le mtime du fichier, noms d'origine
  conservés. Option « captures à part » : PNG (captures d'écran) dans un
  sous-dossier Captures/. Les MP4 (vidéos reçues par messagerie,
  téléchargements, enregistrements d'écran) restent dans le flux mensuel,
  traités comme les autres vidéos.
- « archive » : classement daté avec renommage par horodatage —
    AAAA/AAAA-MM/AAAA-MM-JJ HH-MM-SS.ext
    _LivePhotos/AAAA/AAAA-MM/…ext               (composantes vidéo des Live Photos)
  Une composante Live Photo = un .MOV dont une photo de même nom existe dans
  le même dossier DCIM (IMG_1234.HEIC + IMG_1234.MOV) ; elle est datée et
  nommée d'après SA photo. Les .AAE suivent leur photo (même horodatage,
  même dossier mensuel). Extensions d'origine conservées en minuscules —
  jamais de conversion ; collision à la même seconde (rafale) résolue en
  .~2, .~3… — jamais d'écrasement.

Le renommage utilise l'heure locale du PC. En organisation « archive », la
date vient de l'EXIF lu après copie (voir engine.py) avec le mtime en repli ;
l'identité incrémentale, elle, reste (chemin, taille, mtime).

L'organisation est FIGÉE par destination dès la première synchronisation
(mémorisée dans le manifeste) : en changer exigerait de re-copier ou déplacer
l'existant — refus explicite plutôt que doublons silencieux.

La vérification ne dépend pas de la disposition : le manifeste enregistre le
chemin local réel de chaque fichier, la relecture le suit.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import Iterable, Optional

from applesync.device.base import RemoteFile

CAPTURE_EXTENSIONS = {"PNG"}
PHOTO_EXTENSIONS = {"HEIC", "JPG", "JPEG", "PNG", "DNG", "WEBP", "GIF",
                    "TIF", "TIFF", "BMP"}

# Les albums partagés iCloud (préfixe d'inventaire PhotoCloudSharingData/)
# contiennent des photos d'autres personnes : dans les organisations datées,
# ils vont dans un dossier à part plutôt que mélangés à l'archive.
SHARED_PREFIX = "PhotoCloudSharingData/"
SHARED_DIRNAME = "_AlbumsPartages"


def shared_target(path: str) -> Optional[str]:
    """Cible à part pour un élément d'album partagé, sinon None."""
    if path.startswith(SHARED_PREFIX):
        return f"{SHARED_DIRNAME}/{path[len(SHARED_PREFIX):]}"
    return None


def _ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].upper() if "." in name else ""


def _dir_stem(path: str) -> tuple[str, str]:
    p = PurePosixPath(path)
    return (str(p.parent), p.stem)


class LayoutLockedError(Exception):
    """La destination a déjà une organisation, différente de celle demandée."""

    def __init__(self, locked_id: str, requested_id: str):
        self.locked_id = locked_id
        self.requested_id = requested_id
        super().__init__(
            f"Cette destination a été construite avec l'organisation "
            f"« {label_for(locked_id)} » ; l'option demandée est "
            f"« {label_for(requested_id)} ». L'organisation est figée par la "
            f"première synchronisation : revenez à l'option d'origine, ou "
            f"choisissez une autre destination."
        )


class Layout(ABC):
    id: str = ""
    # Dossier où ranger automatiquement les doublons de contenu détectés
    # pendant la copie (None = pas de rangement, les doublons restent en flux).
    duplicates_dir: Optional[str] = None
    # True : la cible définitive est décidée APRÈS la copie (date EXIF lue sur
    # le fichier local). Le plan n'assigne alors qu'un emplacement de transit.
    finalize_dating: bool = False

    def begin(self, files: Iterable[RemoteFile]) -> None:
        """Appelé une fois avec l'inventaire complet avant les target_for
        (permet l'appariement Live Photo/AAE). Défaut : rien."""

    @abstractmethod
    def target_for(self, f: RemoteFile) -> str:
        """Chemin local relatif (séparateur /) où doit vivre ce fichier."""


class MirrorLayout(Layout):
    id = "miroir"

    def target_for(self, f: RemoteFile) -> str:
        return str(PurePosixPath(f.path))


class DateLayout(Layout):
    def __init__(self, captures_apart: bool = False):
        self.captures_apart = captures_apart
        self.id = "date+captures" if captures_apart else "date"

    def target_for(self, f: RemoteFile) -> str:
        part = shared_target(f.path)
        if part is not None:
            return part
        t = time.localtime(f.mtime)
        base = f"{t.tm_year:04d}/{t.tm_year:04d}-{t.tm_mon:02d}"
        name = f.name
        if self.captures_apart:
            ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
            if ext in CAPTURE_EXTENSIONS:
                base += "/Captures"
        return f"{base}/{name}"


class ArchiveLayout(Layout):
    """Classement daté avec renommage horodaté — voir docstring du module.

    Les doublons de CONTENU (SHA-256 déjà présent au manifeste) sont rangés
    pendant la synchro sous _Doublons/, en conservant la structure
    (_Doublons/AAAA/AAAA-MM/…). Le premier exemplaire rencontré reste dans le
    flux normal ; seuls les exemplaires excédentaires partent en _Doublons.
    La détection a lieu à la copie (le hachage naît là) : rien n'est jamais
    écrasé ni supprimé, seulement rangé ailleurs.
    """

    id = "archive"
    duplicates_dir = "_Doublons"
    finalize_dating = True

    def __init__(self) -> None:
        self._photo_by_key: dict[tuple[str, str], RemoteFile] = {}

    def begin(self, files: Iterable[RemoteFile]) -> None:
        self._photo_by_key = {
            _dir_stem(f.path): f
            for f in files
            if _ext_of(f.path) in PHOTO_EXTENSIONS
        }

    def paired_photo(self, f: RemoteFile) -> Optional[RemoteFile]:
        """La photo jumelle (même dossier, même nom) d'un MOV/AAE, ou None."""
        return self._photo_by_key.get(_dir_stem(f.path))

    def dated_target(self, f: RemoteFile, ts: int, as_live: bool = False) -> str:
        """Cible pour `f` daté de l'epoch `ts` (EXIF ou mtime selon l'appelant)."""
        ext = _ext_of(f.path)
        t = time.localtime(ts)
        stamp = (f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d} "
                 f"{t.tm_hour:02d}-{t.tm_min:02d}-{t.tm_sec:02d}")
        month_dir = f"{t.tm_year:04d}/{t.tm_year:04d}-{t.tm_mon:02d}"
        name = f"{stamp}.{ext.lower()}" if ext else stamp
        if as_live:
            return f"_LivePhotos/{month_dir}/{name}"
        return f"{month_dir}/{name}"

    def target_for(self, f: RemoteFile) -> str:
        """Cible d'après les seuls mtime (sans EXIF) — sert de prévision et de
        repli ; la cible définitive est calculée après copie (finalize_dating)."""
        part = shared_target(f.path)
        if part is not None:
            return part
        ext = _ext_of(f.path)
        paired = self.paired_photo(f) if ext in ("MOV", "AAE") else None
        ref = paired if paired is not None else f
        return self.dated_target(f, ref.mtime, as_live=(ext == "MOV" and paired is not None))


def make_layout(kind: str, captures_apart: bool = False) -> Layout:
    """`kind` : « miroir », « date » ou « archive »."""
    if kind == "miroir":
        return MirrorLayout()
    if kind == "date":
        return DateLayout(captures_apart=captures_apart)
    if kind == "archive":
        return ArchiveLayout()
    raise ValueError(f"organisation inconnue : {kind}")


def layout_from_id(layout_id: str) -> Layout:
    """Reconstruit une stratégie depuis l'identifiant figé au manifeste."""
    if layout_id == "miroir":
        return MirrorLayout()
    if layout_id == "date":
        return DateLayout(False)
    if layout_id == "date+captures":
        return DateLayout(True)
    if layout_id == "archive":
        return ArchiveLayout()
    raise ValueError(f"organisation inconnue : {layout_id}")


def label_for(layout_id: str) -> str:
    return {
        "miroir": "Miroir du DCIM",
        "date": "Par date (AAAA/AAAA-MM)",
        "date+captures": "Par date, captures à part",
        "archive": "Comme l'archive (renommage date, _LivePhotos)",
    }.get(layout_id, layout_id)
