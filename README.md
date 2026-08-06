# AppleSync

[![CI](https://github.com/eljokoGit/applesync/actions/workflows/ci.yml/badge.svg)](https://github.com/eljokoGit/applesync/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/eljokoGit/applesync)](https://github.com/eljokoGit/applesync/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

**Verifiable** backup of an iPhone's photos and videos to a local folder, on
Windows. No conversion: HEIC, HEVC and MOV are copied as they are. No MTP:
everything goes through Apple's own synchronisation protocol (usbmuxd + AFC),
the one iTunes uses.

The goal of the project: being able to delete the originals from the phone
with confidence. Verifiability comes before everything else, and a loud
failure beats a doubtful success.

Coverage includes the camera roll (`DCIM`), the iCloud zone of the photo
library (`CPLAssets`) and iCloud shared albums (filed under
`_SharedAlbums/`).

## Why not MTP

Reaching an iPhone through Windows Explorer (MTP) **truncates silently**: on
the project's test library, three successive enumerations of the same DCIM
folder returned 164, then 124, then 185 folders without raising a single
error, and a "complete" inventory reported 56 GB instead of the real 109 GB.
Unacceptable for a reference backup. AFC, on the other hand, exposes the
library with reliable metadata and positionable reads — exactly what a
checkable inventory and a byte-exact resume need.

## What the application does

- **Inventory first.** The device content is enumerated **twice** and both
  passes are compared. Any divergence stops everything, naming the files that
  differ. No copy ever starts on a doubtful inventory.
- **Incremental and idempotent copy.** A file's identity is (path, size,
  modification time) — not just its name. Files already backed up never go
  again.
- **Byte-exact resume.** The device drops the session when the screen locks:
  the copy resumes exactly where it stopped. A file appears under its final
  name only once complete, checked and hashed — never a partial file
  masquerading as a valid one.
- **Verification by re-reading.** After the copy, every file is **read back
  from disk** and its SHA-256 compared to the one computed during the
  transfer. The output is a list of names, not a percentage.
- **Never deletes.** No write operation towards the device exists in the code
  (structural, not a setting). On the PC side, nothing is overwritten or
  deleted: files that vanished from the phone are kept and reported.
- **Journal and report** per run, enough to reconstruct what happened.

Extras: content-based duplicate detection, album and favourite recovery, and
an inventory stability check.

## Requirements

- Windows 10 or 11, **Python 3.12+**
- The Apple driver listening on `127.0.0.1:27015`, provided by **iTunes**
  (Apple Mobile Device Support), the **Apple Devices** app, or the CopyTrans
  drivers. Quick check:
  `Test-NetConnection 127.0.0.1 -Port 27015`
- A USB data cable (many cables are charge-only).

## Installation

**Without Python** — download `AppleSync.exe` from the
[latest release](https://github.com/eljokoGit/applesync/releases/latest) and
double-click it.

**With Python 3.12+**:

```
git clone https://github.com/eljokoGit/applesync.git
cd applesync
python -m venv .venv
.venv\Scripts\python -m pip install .
```

Run it:

```
.venv\Scripts\applesync
```

On Windows, `run-windows.bat` performs those steps on a double-click (it
creates the environment on first launch, then starts the app).

## Updates

At start-up the application checks whether a newer version exists and shows it
in a banner, with a link to the release notes. **It downloads and installs
nothing by itself**: for a backup tool, a silent update is exactly what you do
not want. One anonymous request to the GitHub API, no data sent; to disable
it, set `"check_updates": false` in
`%LOCALAPPDATA%\AppleSync\config.json`.

To update:

- **executable**: download the new `AppleSync.exe` and replace the old one;
- **Python install**: `git pull`, then
  `.venv\Scripts\python -m pip install .`

Your backups are never touched by an update: the manifest and the history live
in the destination folder, not in the software.

## Usage

The window is the flow: three numbered steps, top to bottom.

1. Plug the device in, unlock it, accept "Trust This Computer". The chip in
   the header turns green.
2. Choose the destination folder and the **layout** — decide before the first
   synchronisation, as it is then frozen for that destination:
   - *Mirror*: the device tree as it is;
   - *By date*: `YYYY/YYYY-MM/`, original names, with an optional
     "screenshots apart" switch;
   - *Archive*: `YYYY/YYYY-MM/YYYY-MM-DD HH-MM-SS.ext` (renamed after the
     EXIF capture date, file date as fallback), Live Photo videos in
     `_LivePhotos/`, content duplicates filed in `_Duplicates/`.
3. **Run inventory**: double enumeration, delta presented for validation.
   Nothing is written at this stage.
4. **Copy to PC**: copy, then automatic deep verification and final report.
5. **Verify**: re-reads and re-hashes the whole destination against a fresh
   device inventory, at any time, independently of a copy.

The **Tools** menu holds what is used occasionally rather than every time:
*Rebuild albums and favourites* (each album becomes a folder of copies),
*List content duplicates* (report only, nothing deleted), *Run stability
check* (three inventories with an unplug between each, to prove enumeration is
reproducible), *Open reports folder*, and *Appearance* — light, dark, or
follow Windows.

Interrupting is always safe: the stop button, a screen lock or an unplugged
cable all lead to the same outcome — what is copied is kept, and the current
file resumes byte-exactly on the next run.

## What ends up in the backup folder

```
<destination>/
  2024/2024-08/…                 photos and videos (per the chosen layout)
  _LivePhotos/                   video part of Live Photos
  _Duplicates/                   surplus copies (identical content)
  _SharedAlbums/                 items of iCloud shared albums
  _Albums/                       rebuilt albums and favourites (optional)
  .applesync/
    manifest.sqlite3             what was copied: identity + SHA-256
    logs/run_*.jsonl             detailed journal of each run
    reports/                     reports and CSV breakdowns
```

The manifest lives in the destination: the backup is self-contained and can be
moved from one disk to another without losing anything.

## Simulation mode (no device)

```
.venv\Scripts\applesync --simulate
.venv\Scripts\applesync --simulate --sim-fault truncate
```

A fake device lets you try the tool out. `--sim-fault` injects the real-world
faults (enumeration truncated without error, disconnection, locked device) so
you can watch the application refuse a doubtful inventory.

## Contributing

Bugs and suggestions go through
[issues](https://github.com/eljokoGit/applesync/issues); the process, the
project rules and the release procedure are in
[CONTRIBUTING.md](CONTRIBUTING.md). For a security flaw, see
[SECURITY.md](SECURITY.md) — never a public issue.

## Development

```
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest tests -q
```

The test suite (110 tests) runs entirely against a deterministic device
simulator, with no hardware: a reproducible file tree, contents generated on
the fly, and injection of the faults seen in the field (silent enumeration
truncation, disconnection mid-run, read failing mid-file, locked device).

The code is split into three layers: `applesync/device/` (device access — an
abstract contract, a real AFC implementation, a simulator),
`applesync/core/` (inventory, plan, copy, verification, reports) and
`applesync/ui/` (PySide6 interface, no device access on the GUI thread).

## Known limitations

- **Windows only** in practice (paths and launcher); the core is portable
  Python, but nothing else is tested.
- **The `pymobiledevice3` dependency is pinned to 10.3.1**: seeking inside a
  file (needed for resume) uses an internal API, since the public one does not
  expose it. Any version bump must be re-validated.
- **Albums**: the iOS Photos database schema is undocumented and changes over
  time. Parsing works by introspection and fails loudly rather than returning
  a partial result. Depending on the iOS version the database may also be
  unreachable — `applesync --probe-albums` checks that.
- **No PC to device transfer.** Adding photos to the library or deleting on
  the device is not possible from a PC over this route; use the photo
  synchronisation of iTunes / Apple Devices.
- Dated renaming uses the PC's local time; photos taken in another time zone
  carry the local zone's time.

## License

MIT — see [LICENSE](LICENSE).
