"""Comparaison de versions pour la vérification de mise à jour.

La logique est testée hors réseau : `check_for_update` ne fait qu'ajouter un
appel HTTP autour de `is_newer`, et doit rendre None sans bruit en cas
d'échec (c'est ce que vérifie le dernier test, avec une URL injoignable)."""

import pytest

from applesync.core.updates import (
    UpdateInfo,
    check_for_update,
    is_newer,
    parse_version,
)


@pytest.mark.parametrize("texte, attendu", [
    ("1.0.0", (1, 0, 0, 3, 0)),
    ("v2.3.4", (2, 3, 4, 3, 0)),
    ("  1.2.3  ", (1, 2, 3, 3, 0)),
    ("1.2.3rc2", (1, 2, 3, 2, 2)),
    ("1.2.3-beta.1", (1, 2, 3, 1, 1)),
    ("", None),
    ("main", None),
    ("1.2", None),
    ("1.2.3.4.5", None),
])
def test_parse_version(texte, attendu):
    assert parse_version(texte) == attendu


@pytest.mark.parametrize("publiee, installee, plus_recente", [
    ("1.0.1", "1.0.0", True),
    ("1.1.0", "1.0.9", True),
    ("2.0.0", "1.9.9", True),
    ("v1.0.1", "1.0.0", True),          # préfixe v toléré
    ("1.0.0", "1.0.0", False),          # identiques
    ("1.0.0", "1.0.1", False),          # publiée plus ancienne
    ("1.0.0", "1.0.0rc1", True),        # stable > pré-version
    ("1.0.0rc1", "1.0.0", False),
    ("1.0.0rc2", "1.0.0rc1", True),
])
def test_is_newer(publiee, installee, plus_recente):
    assert is_newer(publiee, installee) is plus_recente


@pytest.mark.parametrize("publiee, installee", [
    ("", "1.0.0"),
    ("nightly", "1.0.0"),
    ("1.0.1", "inconnue"),
])
def test_version_illisible_ne_signale_rien(publiee, installee):
    """Par prudence : dans le doute, on n'annonce pas de mise à jour."""
    assert is_newer(publiee, installee) is False


def test_echec_reseau_silencieux(monkeypatch):
    """Hors ligne ou API en panne : None, aucune exception — une
    vérification de version ne doit jamais gêner une sauvegarde."""
    import applesync.core.updates as updates

    def boom(*args, **kwargs):
        raise OSError("réseau indisponible")

    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert check_for_update("1.0.0") is None


def test_reponse_valide_donne_un_updateinfo(monkeypatch):
    import io

    import applesync.core.updates as updates

    charge = b'{"tag_name": "v9.9.9", "html_url": "https://example.invalid/r", "name": "9.9.9"}'

    class FausseReponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FausseReponse(charge))
    info = check_for_update("1.0.0")
    assert isinstance(info, UpdateInfo)
    assert info.latest == "9.9.9" and info.current == "1.0.0"
    assert info.url == "https://example.invalid/r"

    # Version déjà à jour : rien à signaler
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FausseReponse(charge))
    assert check_for_update("9.9.9") is None
