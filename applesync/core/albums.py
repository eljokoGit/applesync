"""Récupération des albums et favoris depuis Photos.sqlite (option à part).

Fonction TOTALEMENT indépendante de la sauvegarde : elle lit la base Photos
de l'iPhone (`/PhotoData/Photos.sqlite`, lisible par AFC selon les versions
d'iOS — vérifiable avec `--probe-albums`), la parse LOCALEMENT et matérialise
les albums en COPIES des fichiers déjà sauvegardés : des fichiers ordinaires,
déplaçables partout, au prix de l'espace disque (choix assumé — les liens
physiques, essayés d'abord, imposaient trop de précautions d'usage).
Elle ne modifie jamais la sauvegarde et son échec n'empêche rien.

Défenses (le schéma de Photos.sqlite n'est pas documenté et change à chaque
iOS) :
- copie de la base + son journal -wal, puis PRAGMA quick_check AVANT tout
  parsing ; une base douteuse est re-copiée une fois, puis échec bruyant ;
- aucun nom de table de jointure codé en dur : la table Z_xxASSETS est
  découverte par introspection et VALIDÉE par jointure effective ;
- toute colonne attendue absente → AlbumsSchemaError nominatif (ce qui a été
  trouvé vs attendu), jamais un résultat silencieusement vide ;
- _Albums/ n'est régénéré que s'il porte le marqueur « généré par AppleSync » ;
  un _Albums créé à la main n'est jamais touché.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from applesync.core.manifest import Manifest
from applesync.device.base import DeviceSession

PHOTOS_DB = "/PhotoData/Photos.sqlite"
USER_ALBUM_KIND = 2          # ZGENERICALBUM.ZKIND des albums créés par l'utilisateur
CHUNK = 1024 * 1024
ALBUMS_DIRNAME = "_Albums"
FAVORITES_DIRNAME = "_Favoris"
MARKER = ".applesync-genere"


class AlbumsError(Exception):
    """Récupération d'albums impossible — n'affecte pas la sauvegarde."""


class AlbumsSchemaError(AlbumsError):
    """Le schéma de Photos.sqlite ne correspond pas à ce qui est attendu
    (changement d'iOS probable). Message nominatif pour diagnostic."""


@dataclass
class AlbumsData:
    """Contenu extrait de la base : albums utilisateur et favoris.

    Les chemins sont relatifs au DCIM (« 100APPLE/IMG_0001.HEIC »)."""

    albums: list[tuple[str, list[str]]] = field(default_factory=list)
    favorites: list[str] = field(default_factory=list)
    ignored_assets: list[tuple[str, str]] = field(default_factory=list)  # (asset, raison)
    # Recensement de TOUTE la photothèque (pas seulement les albums) par zone
    # de stockage : « DCIM » est couvert par la sauvegarde, le reste NON.
    library_by_zone: dict[str, int] = field(default_factory=dict)


@dataclass
class AlbumsReport:
    albums_count: int = 0
    copies_created: int = 0
    copied_bytes: int = 0
    favorites_count: int = 0
    csv_path: str = ""
    unmatched: list[tuple[str, str, str]] = field(default_factory=list)  # (album, fichier, raison)
    warnings: list[str] = field(default_factory=list)
    library_by_zone: dict[str, int] = field(default_factory=dict)

    def to_markdown(self) -> str:
        from applesync.core.report import fmt_bytes

        lines = [
            "# Albums récupérés depuis l'iPhone",
            "",
            f"- Albums : **{self.albums_count}** (dans `{ALBUMS_DIRNAME}/`)",
            f"- Fichiers copiés : {self.copies_created} — {fmt_bytes(self.copied_bytes)} "
            f"d'espace disque utilisé",
            f"- Favoris : {self.favorites_count} (dans `{ALBUMS_DIRNAME}/{FAVORITES_DIRNAME}/`)",
            f"- Détail complet : `{self.csv_path}`",
            "",
        ]
        if self.library_by_zone:
            couvertes = {"DCIM", "PhotoData/CPLAssets",
                         "PhotoData/PhotoCloudSharingData"}
            hors = sum(n for z, n in self.library_by_zone.items() if z not in couvertes)
            lines.append("## Où vivent les fichiers de la photothèque")
            lines.append("")
            for zone, n in sorted(self.library_by_zone.items(),
                                  key=lambda x: -x[1]):
                if zone in couvertes:
                    lines.append(f"- `{zone}` : {n} — **couverts par la sauvegarde** ✓")
                else:
                    lines.append(f"- `{zone}` : {n} — **PAS couverts par la sauvegarde** ⚠")
            lines.append("")
            if hors:
                lines.append(
                    f"⚠ **{hors} élément(s) de la photothèque vivent dans une zone "
                    f"non couverte** : ils apparaissent dans l'app Photos mais ne "
                    f"sont pas dans la sauvegarde. À signaler pour décision."
                )
            else:
                lines.append(
                    "Toutes les zones où vit la photothèque sont couvertes par la sauvegarde."
                )
            lines.append("")
        if self.warnings:
            lines.append("## Avertissements")
            lines.append("")
            lines.extend(f"- {w}" for w in self.warnings)
            lines.append("")
        if self.unmatched:
            lines.append(f"## Éléments non appariés : {len(self.unmatched)}")
            lines.append("")
            lines.append("Présents dans un album côté iPhone mais introuvables dans la")
            lines.append("sauvegarde (fichier jamais synchronisé, ou hors DCIM) :")
            lines.append("")
            lines.extend(
                f"- `{f}` (album « {a} ») — {r}" for a, f, r in self.unmatched
            )
            lines.append("")
        if not self.unmatched:
            lines.append("Aucun écart : chaque élément d'album pointe vers un fichier")
            lines.append("de la sauvegarde.")
        return "\n".join(lines)


ProgressCb = Callable[[int, int], None]     # (octets_faits, octets_total)


# ---------------------------------------------------------------------------
# 1. Rapatriement de la base
# ---------------------------------------------------------------------------

def _fetch_one(session: DeviceSession, remote: str, target: Path,
               progress_cb: Optional[ProgressCb], base_done: int, total: int,
               cancel: Optional[Callable[[], bool]] = None) -> int:
    size = session.stat_media(remote)
    part = target.with_name(target.name + ".part")
    done = 0
    with session.open_media(remote) as reader, open(part, "wb") as out:
        while done < size:
            if cancel is not None and cancel():
                raise AlbumsError("récupération des albums interrompue")
            data = reader.read(min(CHUNK, size - done))
            if not data:
                raise AlbumsError(
                    f"{remote} : flux terminé à {done} octets, {size} attendus"
                )
            out.write(data)
            done += len(data)
            if progress_cb:
                progress_cb(base_done + done, total)
        out.flush()
        os.fsync(out.fileno())
    os.replace(part, target)
    return size


def fetch_photos_db(
    session: DeviceSession,
    work_dir: Path,
    progress_cb: Optional[ProgressCb] = None,
    cancel: Optional[Callable[[], bool]] = None,
    phase_cb: Optional[Callable[[str], None]] = None,
) -> Path:
    """Copie Photos.sqlite (+ journal -wal s'il existe) et vérifie l'intégrité.

    Deux tentatives : la base peut être en cours d'écriture sur l'iPhone au
    moment de la copie. Ensuite : échec bruyant."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    last_error = "?"
    for attempt in (1, 2):
        total = session.stat_media(PHOTOS_DB)
        wal_size = 0
        try:
            wal_size = session.stat_media(PHOTOS_DB + "-wal")
        except Exception:
            wal_size = 0
        total += wal_size

        db_local = work_dir / "Photos.sqlite"
        # Un -shm/-wal périmé d'une copie précédente corromprait l'ouverture.
        for suffix in ("-wal", "-shm"):
            (work_dir / ("Photos.sqlite" + suffix)).unlink(missing_ok=True)

        done = _fetch_one(session, PHOTOS_DB, db_local, progress_cb, 0, total,
                          cancel=cancel)
        if wal_size:
            try:
                _fetch_one(session, PHOTOS_DB + "-wal",
                           work_dir / "Photos.sqlite-wal",
                           progress_cb, done, total, cancel=cancel)
            except Exception:
                if cancel is not None and cancel():
                    raise
                # WAL disparu/modifié entre le stat et la lecture : la base
                # seule reste cohérente (état avant les dernières écritures).
                (work_dir / "Photos.sqlite-wal").unlink(missing_ok=True)

        if phase_cb:
            phase_cb("Contrôle d'intégrité de la base copiée…")
        try:
            con = sqlite3.connect(f"file:{db_local}?mode=ro", uri=True)
            try:
                verdict = con.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                con.close()
            if verdict == "ok":
                return db_local
            last_error = f"quick_check : {verdict}"
        except sqlite3.Error as e:
            last_error = f"ouverture impossible : {e}"

    raise AlbumsError(
        f"Photos.sqlite copié deux fois, intègre aucune des deux "
        f"({last_error}). Réessayez iPhone posé (aucune app Photos ouverte)."
    )


# ---------------------------------------------------------------------------
# 2. Parsing défensif
# ---------------------------------------------------------------------------

def _require_columns(con: sqlite3.Connection, table: str, needed: set[str]) -> None:
    try:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error as e:
        raise AlbumsSchemaError(f"table {table} illisible : {e}") from e
    if not cols:
        raise AlbumsSchemaError(f"table {table} absente de Photos.sqlite")
    missing = needed - cols
    if missing:
        raise AlbumsSchemaError(
            f"{table} : colonnes attendues absentes {sorted(missing)} — "
            f"schéma iOS modifié ? Colonnes trouvées : {sorted(cols)[:20]}…"
        )


def _find_join_table(con: sqlite3.Connection) -> tuple[str, str, str]:
    """Découvre la table de jointure albums↔assets (nom variable par iOS).

    Candidat = table Z_%ASSETS avec une colonne *ALBUMS et une colonne
    *ASSETS ; validé par jointure effective sur ZGENERICALBUM et ZASSET."""
    candidates: list[tuple[str, str, str]] = []
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z^_%ASSETS' ESCAPE '^'"
    ).fetchall()
    for (name,) in rows:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
        album_col = next((c for c in cols if c.upper().endswith("ALBUMS")), None)
        asset_col = next(
            (c for c in cols if c.upper().endswith("ASSETS") and c != album_col), None
        )
        if album_col and asset_col:
            candidates.append((name, album_col, asset_col))

    if not candidates:
        raise AlbumsSchemaError(
            "aucune table de jointure albums↔assets reconnue (Z_%ASSETS) — "
            f"tables vues : {[r[0] for r in rows]}"
        )

    validated = []
    for name, album_col, asset_col in candidates:
        try:
            n = con.execute(
                f'SELECT COUNT(*) FROM "{name}" j'
                f' JOIN ZGENERICALBUM g ON g.Z_PK = j."{album_col}"'
                f' JOIN ZASSET s ON s.Z_PK = j."{asset_col}"'
            ).fetchone()[0]
        except sqlite3.Error:
            continue
        validated.append((n, name, album_col, asset_col))

    if not validated:
        raise AlbumsSchemaError(
            f"tables candidates trouvées mais aucune ne joint ZGENERICALBUM et "
            f"ZASSET : {[c[0] for c in candidates]}"
        )
    validated.sort(reverse=True)          # la plus peuplée l'emporte
    _, name, album_col, asset_col = validated[0]
    return name, album_col, asset_col


def _asset_rel_path(directory: Optional[str], filename: Optional[str]) -> tuple[Optional[str], str]:
    """(chemin d'inventaire, raison si inutilisable).

    Deux zones couvertes par la sauvegarde : le DCIM (chemins relatifs) et la
    zone iCloud CPLAssets (chemins préfixés « CPLAssets/ », comme dans
    l'inventaire)."""
    if not filename:
        return None, "nom de fichier vide dans la base"
    d = (directory or "").strip("/")
    if d.startswith("DCIM/"):
        return f"{d[5:]}/{filename}", ""
    if (d.startswith("PhotoData/CPLAssets/") or d == "PhotoData/CPLAssets"
            or d.startswith("PhotoData/PhotoCloudSharingData/")
            or d == "PhotoData/PhotoCloudSharingData"):
        return f"{d[len('PhotoData/'):]}/{filename}", ""
    if d.startswith("DCIM"):
        return None, f"dossier inattendu « {directory} »"
    if not d:
        return None, "hors DCIM (dossier vide)"
    return None, f"hors DCIM (« {directory} »)"


def parse_albums(db_path: Path) -> AlbumsData:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        _require_columns(con, "ZASSET",
                         {"Z_PK", "ZDIRECTORY", "ZFILENAME", "ZFAVORITE", "ZTRASHEDSTATE"})
        _require_columns(con, "ZGENERICALBUM", {"Z_PK", "ZTITLE", "ZKIND", "ZTRASHEDSTATE"})
        join, album_col, asset_col = _find_join_table(con)

        data = AlbumsData()

        rows = con.execute(
            f'SELECT g.Z_PK, g.ZTITLE, s.ZDIRECTORY, s.ZFILENAME'
            f' FROM "{join}" j'
            f' JOIN ZGENERICALBUM g ON g.Z_PK = j."{album_col}"'
            f' JOIN ZASSET s ON s.Z_PK = j."{asset_col}"'
            f' WHERE g.ZKIND = ? AND IFNULL(g.ZTRASHEDSTATE, 0) = 0'
            f'   AND IFNULL(s.ZTRASHEDSTATE, 0) = 0',
            (USER_ALBUM_KIND,),
        ).fetchall()

        par_album: dict[tuple[int, str], list[str]] = {}
        for pk, title, directory, filename in rows:
            key = (pk, title or f"(sans titre) #{pk}")
            rel, raison = _asset_rel_path(directory, filename)
            if rel is None:
                data.ignored_assets.append((f"{directory}/{filename}", raison))
                par_album.setdefault(key, [])
                continue
            par_album.setdefault(key, []).append(rel)

        # Albums vides inclus (l'utilisateur les verra) ; tri alphabétique.
        data.albums = sorted(
            ((titre, sorted(fichiers)) for (_, titre), fichiers in par_album.items()),
            key=lambda x: x[0].casefold(),
        )

        for directory, filename in con.execute(
            "SELECT ZDIRECTORY, ZFILENAME FROM ZASSET"
            " WHERE ZFAVORITE = 1 AND IFNULL(ZTRASHEDSTATE, 0) = 0"
        ):
            rel, raison = _asset_rel_path(directory, filename)
            if rel is None:
                data.ignored_assets.append((f"{directory}/{filename}", raison))
            else:
                data.favorites.append(rel)
        data.favorites.sort()

        # Recensement complet : où vivent les fichiers de la photothèque ?
        # Zone = premier niveau du chemin (DCIM, PhotoData/CPLAssets, …).
        for (directory,) in con.execute(
            "SELECT ZDIRECTORY FROM ZASSET WHERE IFNULL(ZTRASHEDSTATE, 0) = 0"
        ):
            d = (directory or "").strip("/")
            if d.startswith("DCIM"):
                zone = "DCIM"
            elif not d:
                zone = "(dossier vide)"
            else:
                parts = d.split("/")
                zone = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
            data.library_by_zone[zone] = data.library_by_zone.get(zone, 0) + 1
        return data
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 3. Matérialisation
# ---------------------------------------------------------------------------

_FORBIDDEN = '<>:"/\\|?*'
_CSV_HEADER = "album;fichier_sauvegarde;source_iphone"


def _genere_par_nous(root: Path) -> bool:
    """Le dossier _Albums est-il l'un des nôtres ?

    Marqueur en premier ; à défaut, notre albums.csv fait foi (le marqueur
    peut disparaître si une reconstruction a été interrompue par un fichier
    verrouillé, typiquement le CSV resté ouvert dans un tableur)."""
    if (root / MARKER).exists():
        return True
    csv_file = root / "albums.csv"
    if csv_file.exists():
        try:
            with open(csv_file, encoding="utf-8-sig") as fh:
                return fh.readline().strip() == _CSV_HEADER
        except OSError:
            return False
    return False


def _sanitize(name: str) -> str:
    clean = "".join("_" if c in _FORBIDDEN or ord(c) < 32 else c for c in name)
    clean = clean.strip(" .")
    return clean or "_album"


def _latest_local(manifest: Manifest, rel: str) -> Optional[str]:
    entries = manifest.entries_for_path(rel)
    if not entries:
        return None
    return max(entries, key=lambda e: e.synced_at).local_path


def materialize_albums(
    data: AlbumsData,
    manifest: Manifest,
    dest_root: Path,
    progress_cb: Optional[ProgressCb] = None,   # (fichiers faits, fichiers total)
) -> AlbumsReport:
    """(Re)génère `_Albums/` : un dossier par album, COPIES des fichiers de
    la sauvegarde (fichiers ordinaires), `_Favoris/`, et un CSV récapitulatif.

    `_Albums/` n'est détruit et reconstruit QUE s'il porte le marqueur
    « généré par AppleSync » — un dossier fait main n'est jamais touché."""
    dest_root = Path(dest_root)
    root = dest_root / ALBUMS_DIRNAME
    marker = root / MARKER
    if root.exists():
        if not _genere_par_nous(root):
            raise AlbumsError(
                f"{ALBUMS_DIRNAME}/ existe mais ne ressemble pas à un dossier "
                f"généré par AppleSync — par prudence, déplacez-le ou "
                f"supprimez-le vous-même, puis re-cliquez « Albums »."
            )
        try:
            shutil.rmtree(root)
        except OSError as e:
            # Fichier verrouillé (albums.csv ouvert dans Excel ?) : on re-marque
            # le dossier comme nôtre pour le prochain essai, et on explique.
            try:
                if root.exists():
                    marker.write_text("généré par AppleSync\n", encoding="utf-8")
            except OSError:
                pass
            raise AlbumsError(
                f"Impossible de reconstruire {ALBUMS_DIRNAME}/ : un de ses "
                f"fichiers est ouvert dans un autre programme (albums.csv "
                f"dans Excel ?). Fermez-le puis re-cliquez « Albums ». "
                f"Détail : {e}"
            ) from e
    root.mkdir(parents=True)
    marker.write_text(
        "Dossier généré par AppleSync (copies des fichiers de la sauvegarde).\n"
        "Regénéré à chaque récupération d'albums : n'y rangez rien à la main.\n",
        encoding="utf-8",
    )

    report = AlbumsReport(albums_count=len(data.albums),
                          favorites_count=len(data.favorites),
                          library_by_zone=dict(data.library_by_zone))

    def copy_into(folder: Path, rel_local: str) -> int:
        """Copie le fichier de la sauvegarde dans le dossier d'album.
        Retourne le nombre d'octets copiés."""
        src = dest_root / rel_local
        base = Path(rel_local).name
        dst = folder / base
        n = 2
        while dst.exists():
            p = Path(base)
            dst = folder / f"{p.stem}.~{n}{p.suffix}"
            n += 1
        shutil.copy2(src, dst)
        return dst.stat().st_size

    groupes: list[tuple[str, str, list[str]]] = [
        (titre, _sanitize(titre), fichiers) for titre, fichiers in data.albums
    ]
    groupes.append(("Favoris", FAVORITES_DIRNAME, data.favorites))
    total_prevu = sum(len(fichiers) for _, _, fichiers in groupes)
    faits = 0

    csv_path = root / "albums.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["album", "fichier_sauvegarde", "source_iphone"])

        vus: set[str] = set()
        for titre, dossier_nom, fichiers in groupes:
            dossier = root / dossier_nom
            n = 2
            while dossier.name in vus:
                dossier = root / f"{dossier_nom}~{n}"
                n += 1
            vus.add(dossier.name)
            dossier.mkdir(parents=True, exist_ok=True)

            for rel in fichiers:
                faits += 1
                if progress_cb:
                    progress_cb(faits, total_prevu)
                local = _latest_local(manifest, rel)
                if local is None:
                    report.unmatched.append(
                        (titre, rel, "jamais synchronisé (lancez une synchro)")
                    )
                    continue
                if not (dest_root / local).exists():
                    report.unmatched.append(
                        (titre, rel, f"absent du disque ({local}) — vérifiez la destination")
                    )
                    continue
                w.writerow([titre, local, rel])
                report.copied_bytes += copy_into(dossier, local)
                report.copies_created += 1

        for asset, raison in data.ignored_assets:
            w.writerow(["(ignoré)", "", f"{asset} — {raison}"])

    if data.ignored_assets:
        report.warnings.append(
            f"{len(data.ignored_assets)} élément(s) de la base ignoré(s) "
            f"(hors DCIM) — listés en fin de CSV."
        )
    report.csv_path = str(csv_path)
    return report


def save_report(report: AlbumsReport, dest_root: Path) -> Path:
    out_dir = Path(dest_root) / ".applesync" / "rapports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"albums_{time.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path
