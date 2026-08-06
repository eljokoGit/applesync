"""Stratégies d'organisation : cibles exactes, appariement Live Photo/AAE."""

import time

import pytest

from applesync.core.layout import (
    ArchiveLayout,
    DateLayout,
    LayoutLockedError,
    MirrorLayout,
    label_for,
    layout_from_id,
    make_layout,
)
from applesync.device.base import RemoteFile


def _f(path: str, mtime: int, size: int = 1000) -> RemoteFile:
    return RemoteFile(path=path, size=size, mtime=mtime)


# mtime de référence : 2024-08-15 14:30:22 heure locale
T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_miroir_passthrough():
    assert MirrorLayout().target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "100APPLE/IMG_0001.HEIC"


def test_date_annee_mois_nom_conserve():
    lay = DateLayout(captures_apart=False)
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/IMG_0001.HEIC"
    # PNG non séparé quand l'option est désactivée
    assert lay.target_for(_f("100APPLE/IMG_0002.PNG", T)) == \
        "2024/2024-08/IMG_0002.PNG"


def test_date_captures_a_part():
    lay = DateLayout(captures_apart=True)
    assert lay.target_for(_f("100APPLE/IMG_0002.PNG", T)) == \
        "2024/2024-08/Captures/IMG_0002.PNG"
    # Les MP4 sont des vidéos normales (messagerie, imports) : flux mensuel,
    # jamais dans Captures.
    assert lay.target_for(_f("100APPLE/IMG_0003.MP4", T)) == \
        "2024/2024-08/IMG_0003.MP4"
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/IMG_0001.HEIC"


def test_archive_renommage_horodate():
    lay = ArchiveLayout()
    lay.begin([])
    assert lay.target_for(_f("100APPLE/IMG_0001.HEIC", T)) == \
        "2024/2024-08/2024-08-15 14-30-22.heic"


def test_archive_live_photo_appariee():
    """Le MOV d'une Live Photo (photo de même nom, même dossier) part dans
    _LivePhotos et prend l'horodatage de SA photo."""
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    live = _f("100APPLE/IMG_0001.MOV", T + 5)      # mtime légèrement décalé
    video = _f("100APPLE/IMG_0002.MOV", T + 100)   # vraie vidéo, sans photo
    lay = ArchiveLayout()
    lay.begin([photo, live, video])
    assert lay.target_for(photo) == "2024/2024-08/2024-08-15 14-30-22.heic"
    assert lay.target_for(live) == \
        "_LivePhotos/2024/2024-08/2024-08-15 14-30-22.mov"
    assert lay.target_for(video) == "2024/2024-08/2024-08-15 14-32-02.mov"


def test_archive_aae_suit_sa_photo():
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    aae = _f("100APPLE/IMG_0001.AAE", T + 86400 * 30)   # retouche un mois après
    lay = ArchiveLayout()
    lay.begin([photo, aae])
    # L'AAE reste dans le dossier mensuel de la photo, même horodatage
    assert lay.target_for(aae) == "2024/2024-08/2024-08-15 14-30-22.aae"


def test_archive_appariement_limite_au_meme_dossier():
    photo = _f("100APPLE/IMG_0001.HEIC", T)
    autre = _f("101APPLE/IMG_0001.MOV", T + 5)   # même nom, AUTRE dossier
    lay = ArchiveLayout()
    lay.begin([photo, autre])
    assert lay.target_for(autre).startswith("2024/")       # pas _LivePhotos


def test_fabrique_et_ids():
    assert make_layout("miroir").id == "miroir"
    assert make_layout("date").id == "date"
    assert make_layout("date", captures_apart=True).id == "date+captures"
    assert make_layout("archive").id == "archive"
    for lid in ("miroir", "date", "date+captures", "archive"):
        assert layout_from_id(lid).id == lid
        assert label_for(lid) != lid   # libellé humain défini
    with pytest.raises(ValueError):
        make_layout("inconnu")


def test_erreur_verrou_message_actionnable():
    e = LayoutLockedError("miroir", "archive")
    assert "figée" in str(e) and "Miroir" in str(e)
