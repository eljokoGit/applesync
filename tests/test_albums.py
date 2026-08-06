"""Albums : parsing défensif de Photos.sqlite et matérialisation en liens.

La base de test reproduit le schéma Core Data réel (ZASSET, ZGENERICALBUM,
table de jointure au nom variable par iOS — ici Z_28ASSETS)."""

import sqlite3

import pytest

from applesync.core.albums import (
    AlbumsError,
    AlbumsSchemaError,
    fetch_photos_db,
    materialize_albums,
    parse_albums,
    save_report,
)
from applesync.core.engine import SyncEngine
from applesync.core.manifest import Manifest
from applesync.device.simulator import SimProfile, SimulatedBackend


def _fixture_db(path, join_table="Z_28ASSETS", album_col="Z_28ALBUMS",
                asset_col="Z_3ASSETS", with_directory=True):
    con = sqlite3.connect(str(path))
    dir_col = "ZDIRECTORY VARCHAR," if with_directory else ""
    con.executescript(f"""
        CREATE TABLE ZASSET (
            Z_PK INTEGER PRIMARY KEY, {dir_col} ZFILENAME VARCHAR,
            ZFAVORITE INTEGER DEFAULT 0, ZTRASHEDSTATE INTEGER DEFAULT 0
        );
        CREATE TABLE ZGENERICALBUM (
            Z_PK INTEGER PRIMARY KEY, ZTITLE VARCHAR, ZKIND INTEGER,
            ZTRASHEDSTATE INTEGER DEFAULT 0
        );
        CREATE TABLE "{join_table}" (
            "{album_col}" INTEGER, "{asset_col}" INTEGER
        );
    """)
    return con


def _populate(con):
    assets = [
        (1, "DCIM/100APPLE", "IMG_0001.HEIC", 0, 0),
        (2, "DCIM/100APPLE", "IMG_0002.HEIC", 1, 0),   # favori
        (3, "DCIM/101APPLE", "IMG_0100.JPG", 0, 0),
        (4, "DCIM/101APPLE", "IMG_0101.JPG", 0, 1),    # à la corbeille
        (5, None, "hors_dcim.png", 1, 0),              # hors DCIM
        (6, "PhotoData/CPLAssets/group159", "UUID.JPG", 0, 0),   # zone iCloud
    ]
    con.executemany("INSERT INTO ZASSET VALUES (?,?,?,?,?)", assets)
    con.executemany(
        "INSERT INTO ZGENERICALBUM VALUES (?,?,?,?)",
        [
            (10, "Vacances", 2, 0),
            (11, "Sans titre", 2, 0),
            (12, "Corbeille-album", 2, 1),   # album supprimé
            (13, "Dossier", 4000, 0),        # pas un album utilisateur
        ],
    )
    con.execute("UPDATE ZGENERICALBUM SET ZTITLE=NULL WHERE Z_PK=11")
    con.executemany(
        'INSERT INTO "Z_28ASSETS" VALUES (?,?)',
        [
            (10, 1), (10, 3),     # Vacances : 2 éléments DCIM
            (10, 6),              # + 1 élément zone iCloud (CPLAssets, couvert)
            (11, 2),              # album sans titre
            (12, 1),              # album à la corbeille (exclu)
            (13, 1),              # dossier (exclu)
            (10, 4),              # asset à la corbeille (exclu)
            (10, 5),              # hors zones couvertes (ignoré nominalement)
        ],
    )
    con.commit()


def test_parse_albums_et_favoris(tmp_path):
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()

    data = parse_albums(db)
    titres = dict(data.albums)
    assert "Vacances" in titres
    assert titres["Vacances"] == [
        "100APPLE/IMG_0001.HEIC",
        "101APPLE/IMG_0100.JPG",
        "CPLAssets/group159/UUID.JPG",   # zone iCloud : couverte, donc mappée
    ]
    assert "(sans titre) #11" in titres
    assert "Corbeille-album" not in titres
    assert "Dossier" not in titres
    assert data.favorites == ["100APPLE/IMG_0002.HEIC"]
    assert any("hors DCIM" in raison for _, raison in data.ignored_assets)
    # Recensement complet par zone : la corbeille exclue, le reste compté
    assert data.library_by_zone == {
        "DCIM": 3,
        "(dossier vide)": 1,
        "PhotoData/CPLAssets": 1,
    }


def test_zone_hors_dcim_visible_au_rapport(backend, dest, tmp_path):
    """Le rapport doit dire clairement combien d'éléments de la photothèque
    vivent hors DCIM (donc hors sauvegarde) — jamais un silence."""
    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)
    with Manifest(dest) as m:
        report = materialize_albums(data, m, dest)
    md = report.to_markdown()
    # CPLAssets est désormais couverte par la sauvegarde ; la zone inconnue
    # (dossier vide), elle, doit rester signalée comme un trou.
    assert "`PhotoData/CPLAssets` : 1 — **couverts par la sauvegarde** ✓" in md
    assert "PAS couverts par la sauvegarde" in md
    assert "zone non couverte" in md


def test_jointure_decouverte_quel_que_soit_le_numero(tmp_path):
    """Le numéro de la table de jointure change à chaque iOS : Z_31ASSETS
    doit être découvert aussi bien que Z_28ASSETS."""
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db, join_table="Z_31ASSETS", album_col="Z_31ALBUMS",
                      asset_col="Z_47ASSETS")
    con.execute("INSERT INTO ZASSET VALUES (1,'DCIM/100APPLE','A.HEIC',0,0)")
    con.execute("INSERT INTO ZGENERICALBUM VALUES (10,'X',2,0)")
    con.execute('INSERT INTO "Z_31ASSETS" VALUES (10,1)')
    con.commit()
    con.close()
    data = parse_albums(db)
    assert data.albums == [("X", ["100APPLE/A.HEIC"])]


def test_schema_derive_echec_bruyant(tmp_path):
    """Colonne disparue (changement iOS) → erreur nominative, jamais un
    résultat vide silencieux."""
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db, with_directory=False)
    con.commit()
    con.close()
    with pytest.raises(AlbumsSchemaError) as exc:
        parse_albums(db)
    assert "ZDIRECTORY" in str(exc.value)


def test_jointure_absente_echec_bruyant(tmp_path):
    db = tmp_path / "Photos.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZDIRECTORY VARCHAR,
            ZFILENAME VARCHAR, ZFAVORITE INTEGER, ZTRASHEDSTATE INTEGER);
        CREATE TABLE ZGENERICALBUM (Z_PK INTEGER PRIMARY KEY, ZTITLE VARCHAR,
            ZKIND INTEGER, ZTRASHEDSTATE INTEGER);
    """)
    con.commit()
    con.close()
    with pytest.raises(AlbumsSchemaError):
        parse_albums(db)


def _sync_mirror(backend, dest):
    engine = SyncEngine(backend, dest)
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"


def test_materialisation_liens_et_csv(backend, dest, tmp_path):
    _sync_mirror(backend, dest)
    deux = [f.path for f in backend.tree[:2]]

    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    con.executemany(
        "INSERT INTO ZASSET VALUES (?,?,?,?,?)",
        [
            (1, f"DCIM/{deux[0].rsplit('/', 1)[0]}", deux[0].rsplit("/", 1)[1], 1, 0),
            (2, f"DCIM/{deux[1].rsplit('/', 1)[0]}", deux[1].rsplit("/", 1)[1], 0, 0),
            (3, "DCIM/999APPLE", "JAMAIS_SYNC.HEIC", 0, 0),
        ],
    )
    con.execute("INSERT INTO ZGENERICALBUM VALUES (10,'Été: <2023>?',2,0)")
    con.executemany('INSERT INTO "Z_28ASSETS" VALUES (?,?)',
                    [(10, 1), (10, 2), (10, 3)])
    con.commit()
    con.close()

    data = parse_albums(db)
    with Manifest(dest) as m:
        report = materialize_albums(data, m, dest)

    # Nom d'album nettoyé pour Windows, copies présentes, contenu identique
    album_dir = dest / "_Albums" / "Été_ _2023__"
    assert album_dir.is_dir()
    copies = sorted(p.name for p in album_dir.iterdir())
    assert len(copies) == 2
    src0 = dest / deux[0]
    copie0 = album_dir / deux[0].rsplit("/", 1)[1]
    assert copie0.read_bytes() == src0.read_bytes()
    assert report.copied_bytes >= copie0.stat().st_size

    # Favoris matérialisés, non-synchronisé nommé, CSV présent
    assert (dest / "_Albums" / "_Favoris").is_dir()
    assert report.favorites_count == 1
    assert [u[1] for u in report.unmatched] == ["999APPLE/JAMAIS_SYNC.HEIC"]
    assert (dest / "_Albums" / "albums.csv").exists()
    md = report.to_markdown()
    assert "JAMAIS_SYNC.HEIC" in md

    # Régénération : le marqueur autorise la reconstruction
    with Manifest(dest) as m:
        report2 = materialize_albums(data, m, dest)
    assert report2.copies_created == report.copies_created

    chemin = save_report(report2, dest)
    assert chemin.exists()


def test_marqueur_perdu_mais_csv_a_nous_reconstruit(backend, dest, tmp_path):
    """Reconstruction interrompue = marqueur disparu mais albums.csv présent :
    le dossier est reconnu comme nôtre et régénéré sans blocage (cas du
    CSV resté ouvert dans un tableur pendant la régénération)."""
    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)

    with Manifest(dest) as m:
        materialize_albums(data, m, dest)
        # On simule la reconstruction interrompue : marqueur perdu, CSV présent
        (dest / "_Albums" / ".applesync-genere").unlink()
        report = materialize_albums(data, m, dest)   # ne doit PAS lever
    assert (dest / "_Albums" / ".applesync-genere").exists()
    assert report.albums_count == len(data.albums)


def test_fichier_verrouille_message_actionnable(backend, dest, tmp_path, monkeypatch):
    """Un fichier de _Albums ouvert ailleurs (tableur…) : message clair, et le
    dossier reste reconnu comme nôtre pour le prochain essai.

    Le verrou est simulé plutôt que réel : seul Windows interdit de supprimer
    un fichier ouvert, et c'est la logique de récupération qu'on veut tester,
    pas le système de fichiers."""
    import applesync.core.albums as albums_mod

    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)

    with Manifest(dest) as m:
        materialize_albums(data, m, dest)

        def rmtree_bloque(*args, **kwargs):
            raise OSError(13, "Le fichier est utilisé par un autre processus")

        monkeypatch.setattr(albums_mod.shutil, "rmtree", rmtree_bloque)
        with pytest.raises(AlbumsError) as exc:
            materialize_albums(data, m, dest)
        assert "ouvert dans un autre programme" in str(exc.value)
        # Le marqueur a été reposé : le dossier reste reconnu comme nôtre
        assert (dest / "_Albums" / ".applesync-genere").exists()

        # Verrou levé : la régénération repasse
        monkeypatch.undo()
        report = materialize_albums(data, m, dest)
    assert report.albums_count == len(data.albums)


def test_albums_fait_main_jamais_touche(backend, dest, tmp_path):
    _sync_mirror(backend, dest)
    fait_main = dest / "_Albums"
    fait_main.mkdir()
    (fait_main / "precieux.txt").write_text("à ne pas perdre", encoding="utf-8")

    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    con.commit()
    con.close()
    data = parse_albums(db)
    with Manifest(dest) as m:
        with pytest.raises(AlbumsError) as exc:
            materialize_albums(data, m, dest)
    assert "ne ressemble pas" in str(exc.value)
    assert (fait_main / "precieux.txt").exists()


def test_fetch_depuis_le_simulateur(backend, dest, tmp_path):
    """Bout-en-bout : la base est servie par le jail Media simulé, copiée,
    vérifiée (quick_check) puis parsée."""
    db = tmp_path / "source.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    backend.media_files["/PhotoData/Photos.sqlite"] = db.read_bytes()

    with backend.connect(backend.INFO.udid) as session:
        avancement = []
        local = fetch_photos_db(
            session, dest / ".applesync" / "photodata",
            progress_cb=lambda a, b: avancement.append((a, b)),
        )
    assert local.exists()
    assert avancement and avancement[-1][0] == avancement[-1][1]
    data = parse_albums(local)
    assert dict(data.albums)["Vacances"] == [
        "100APPLE/IMG_0001.HEIC",
        "101APPLE/IMG_0100.JPG",
        "CPLAssets/group159/UUID.JPG",
    ]


def test_fetch_base_corrompue_echec_bruyant(backend, dest):
    backend.media_files["/PhotoData/Photos.sqlite"] = b"SQLite format 3\x00" + b"\x00" * 500
    with backend.connect(backend.INFO.udid) as session:
        with pytest.raises(AlbumsError) as exc:
            fetch_photos_db(session, dest / ".applesync" / "photodata")
    assert "intègre" in str(exc.value) or "ouverture" in str(exc.value)
