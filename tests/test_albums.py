"""Albums: defensive parsing of Photos.sqlite and materialisation as copies.

The test database reproduces the real Core Data schema (ZASSET,
ZGENERICALBUM, and a join table whose name varies by iOS — Z_28ASSETS here).
"""

import csv
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
from applesync.core.engine import COMPLETED, SyncEngine
from applesync.core.manifest import Manifest


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
        (2, "DCIM/100APPLE", "IMG_0002.HEIC", 1, 0),   # favourite
        (3, "DCIM/101APPLE", "IMG_0100.JPG", 0, 0),
        (4, "DCIM/101APPLE", "IMG_0101.JPG", 0, 1),    # trashed
        (5, None, "outside.png", 1, 0),                # uncovered zone
        (6, "PhotoData/CPLAssets/group159", "UUID.JPG", 0, 0),   # iCloud zone
    ]
    con.executemany("INSERT INTO ZASSET VALUES (?,?,?,?,?)", assets)
    con.executemany(
        "INSERT INTO ZGENERICALBUM VALUES (?,?,?,?)",
        [
            (10, "Holidays", 2, 0),
            (11, "Untitled", 2, 0),
            (12, "Trashed-album", 2, 1),     # deleted album
            (13, "Folder", 4000, 0),         # not a user album
        ],
    )
    con.execute("UPDATE ZGENERICALBUM SET ZTITLE=NULL WHERE Z_PK=11")
    con.executemany(
        'INSERT INTO "Z_28ASSETS" VALUES (?,?)',
        [
            (10, 1), (10, 3),     # Holidays: 2 DCIM items
            (10, 6),              # + 1 iCloud-zone item (covered)
            (11, 2),              # untitled album
            (12, 1),              # trashed album (excluded)
            (13, 1),              # folder (excluded)
            (10, 4),              # trashed asset (excluded)
            (10, 5),              # outside covered zones (named as ignored)
        ],
    )
    con.commit()


def test_parses_albums_and_favourites(tmp_path):
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()

    data = parse_albums(db)
    titles = dict(data.albums)
    assert "Holidays" in titles
    assert titles["Holidays"] == [
        "100APPLE/IMG_0001.HEIC",
        "101APPLE/IMG_0100.JPG",
        "CPLAssets/group159/UUID.JPG",   # iCloud zone: covered, so mapped
    ]
    assert "(untitled) #11" in titles
    assert "Trashed-album" not in titles
    assert "Folder" not in titles
    assert data.favorites == ["100APPLE/IMG_0002.HEIC"]
    assert any("outside covered zones" in reason
               for _, reason in data.ignored_assets)
    # Full census by zone: trashed excluded, everything else counted
    assert data.library_by_zone == {
        "DCIM": 3,
        "(empty folder)": 1,
        "PhotoData/CPLAssets": 1,
    }


def test_join_table_is_discovered_whatever_its_number(tmp_path):
    """The join table number changes with every iOS release: Z_31ASSETS must
    be discovered just as well as Z_28ASSETS."""
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


def test_schema_drift_fails_loudly(tmp_path):
    """A column gone (iOS change) raises a named error, never a silently
    empty result."""
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db, with_directory=False)
    con.commit()
    con.close()
    with pytest.raises(AlbumsSchemaError) as exc:
        parse_albums(db)
    assert "ZDIRECTORY" in str(exc.value)


def test_missing_join_table_fails_loudly(tmp_path):
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
    assert report.status == COMPLETED


def test_materialisation_copies_and_csv(backend, dest, tmp_path):
    _sync_mirror(backend, dest)
    two = [f.path for f in backend.tree[:2]]

    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    con.executemany(
        "INSERT INTO ZASSET VALUES (?,?,?,?,?)",
        [
            (1, f"DCIM/{two[0].rsplit('/', 1)[0]}", two[0].rsplit("/", 1)[1], 1, 0),
            (2, f"DCIM/{two[1].rsplit('/', 1)[0]}", two[1].rsplit("/", 1)[1], 0, 0),
            (3, "DCIM/999APPLE", "NEVER_SYNCED.HEIC", 0, 0),
        ],
    )
    con.execute("INSERT INTO ZGENERICALBUM VALUES (10,'Summer: <2023>?',2,0)")
    con.executemany('INSERT INTO "Z_28ASSETS" VALUES (?,?)',
                    [(10, 1), (10, 2), (10, 3)])
    con.commit()
    con.close()

    data = parse_albums(db)
    with Manifest(dest) as m:
        report = materialize_albums(data, m, dest)

    # Album name sanitised for Windows, copies present with identical content
    album_dir = dest / "_Albums" / "Summer_ _2023__"
    assert album_dir.is_dir()
    copies = sorted(p.name for p in album_dir.iterdir())
    assert len(copies) == 2
    src0 = dest / two[0]
    copy0 = album_dir / two[0].rsplit("/", 1)[1]
    assert copy0.read_bytes() == src0.read_bytes()
    assert report.copied_bytes >= copy0.stat().st_size

    # Favourites materialised, never-synced item named, CSV present
    assert (dest / "_Albums" / "_Favorites").is_dir()
    assert report.favorites_count == 1
    assert [u[1] for u in report.unmatched] == ["999APPLE/NEVER_SYNCED.HEIC"]
    csv_path = dest / "_Albums" / "albums.csv"
    assert csv_path.exists()
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh, delimiter=";"))
    assert header == ["album", "backup_file", "device_source"]
    md = report.to_markdown()
    assert "NEVER_SYNCED.HEIC" in md

    # Regeneration: the marker allows the rebuild
    with Manifest(dest) as m:
        report2 = materialize_albums(data, m, dest)
    assert report2.copies_created == report.copies_created

    path = save_report(report2, dest)
    assert path.exists()


def test_uncovered_zone_is_visible_in_the_report(backend, dest, tmp_path):
    """The report must state plainly how many library items live outside the
    covered zones (hence outside the backup) — never a silence."""
    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)
    with Manifest(dest) as m:
        report = materialize_albums(data, m, dest)
    md = report.to_markdown()
    # CPLAssets is covered now; the unknown zone (empty folder) must still be
    # flagged as a hole.
    assert "`PhotoData/CPLAssets`: 1 — **covered by the backup** ✓" in md
    assert "NOT covered by the backup" in md
    assert "uncovered zone" in md


def test_lost_marker_but_our_csv_still_rebuilds(backend, dest, tmp_path):
    """An interrupted rebuild leaves the marker gone but albums.csv present:
    the folder is still recognised as ours and regenerated without blocking."""
    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)

    with Manifest(dest) as m:
        materialize_albums(data, m, dest)
        (dest / "_Albums" / ".applesync-generated").unlink()
        report = materialize_albums(data, m, dest)   # must NOT raise
    assert (dest / "_Albums" / ".applesync-generated").exists()
    assert report.albums_count == len(data.albums)


def test_locked_file_gives_an_actionable_message(backend, dest, tmp_path, monkeypatch):
    """A file of _Albums open elsewhere (a spreadsheet…): clear message, and
    the folder stays recognised as ours for the next attempt.

    The lock is simulated rather than real: only Windows forbids deleting an
    open file, and what we want to test is the recovery logic, not the file
    system."""
    import applesync.core.albums as albums_mod

    _sync_mirror(backend, dest)
    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    data = parse_albums(db)

    with Manifest(dest) as m:
        materialize_albums(data, m, dest)

        def rmtree_blocked(*args, **kwargs):
            raise OSError(13, "The file is in use by another process")

        monkeypatch.setattr(albums_mod.shutil, "rmtree", rmtree_blocked)
        with pytest.raises(AlbumsError) as exc:
            materialize_albums(data, m, dest)
        assert "open in another program" in str(exc.value)
        # The marker was put back: the folder stays recognised as ours
        assert (dest / "_Albums" / ".applesync-generated").exists()

        # Lock released: the rebuild goes through again
        monkeypatch.undo()
        report = materialize_albums(data, m, dest)
    assert report.albums_count == len(data.albums)


def test_a_hand_made_albums_folder_is_never_touched(backend, dest, tmp_path):
    _sync_mirror(backend, dest)
    hand_made = dest / "_Albums"
    hand_made.mkdir()
    (hand_made / "precious.txt").write_text("do not lose", encoding="utf-8")

    db = tmp_path / "Photos.sqlite"
    con = _fixture_db(db)
    con.commit()
    con.close()
    data = parse_albums(db)
    with Manifest(dest) as m:
        with pytest.raises(AlbumsError) as exc:
            materialize_albums(data, m, dest)
    assert "does not look like" in str(exc.value)
    assert (hand_made / "precious.txt").exists()


def test_fetch_from_the_simulator(backend, dest, tmp_path):
    """End to end: the database is served by the simulated Media jail, copied,
    integrity-checked (quick_check) and parsed."""
    db = tmp_path / "source.sqlite"
    con = _fixture_db(db)
    _populate(con)
    con.close()
    backend.media_files["/PhotoData/Photos.sqlite"] = db.read_bytes()

    with backend.connect(backend.INFO.udid) as session:
        progress = []
        local = fetch_photos_db(
            session, dest / ".applesync" / "photodata",
            progress_cb=lambda a, b: progress.append((a, b)),
        )
    assert local.exists()
    assert progress and progress[-1][0] == progress[-1][1]
    data = parse_albums(local)
    assert dict(data.albums)["Holidays"] == [
        "100APPLE/IMG_0001.HEIC",
        "101APPLE/IMG_0100.JPG",
        "CPLAssets/group159/UUID.JPG",
    ]


def test_corrupt_database_fails_loudly(backend, dest):
    backend.media_files["/PhotoData/Photos.sqlite"] = \
        b"SQLite format 3\x00" + b"\x00" * 500
    with backend.connect(backend.INFO.udid) as session:
        with pytest.raises(AlbumsError) as exc:
            fetch_photos_db(session, dest / ".applesync" / "photodata")
    assert "sound" in str(exc.value) or "cannot open" in str(exc.value)
