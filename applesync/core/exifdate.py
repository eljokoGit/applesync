"""Lecture de la date de prise de vue EXIF d'un fichier LOCAL.

Utilisé par l'organisation « archive » APRÈS la copie (le fichier est alors
sur disque : lecture d'en-tête, coût négligeable). Jamais utilisé pour
l'identité incrémentale, qui reste (chemin, taille, mtime) — décision § 4.

Pillow lit l'EXIF des JPEG nativement ; pour les HEIC il échoue proprement
(pas de décodeur) et on retombe sur le mtime — sans gravité : les HEIC sont
les photos prises par l'iPhone lui-même, dont le mtime est fiable. Le
problème que résout ce module, ce sont les JPG anciens dont le mtime est une
date d'import en masse.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

_DATETIME_ORIGINAL = 36867      # Exif IFD : DateTimeOriginal
_DATETIME = 306                 # IFD0 : DateTime (repli)
_EXIF_IFD = 0x8769


def exif_timestamp(path: Path) -> Optional[int]:
    """Epoch (heure locale) de la prise de vue EXIF, ou None si introuvable.

    Ne lève jamais : tout échec (format illisible, EXIF absent, date
    aberrante) rend None et le mtime prendra le relais.
    """
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            raw = None
            try:
                raw = exif.get_ifd(_EXIF_IFD).get(_DATETIME_ORIGINAL)
            except Exception:
                raw = None
            if not raw:
                raw = exif.get(_DATETIME)
        if not raw or not isinstance(raw, str):
            return None
        st = time.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S")
        if st.tm_year < 1990 or st.tm_year > 2100:
            return None
        return int(time.mktime(st))
    except Exception:
        return None
