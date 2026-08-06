# Changelog

Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [semantic versioning](https://semver.org/).

## [Unreleased]

## [1.2.0] — 2026-08-06

Interface only, again: the core, the device layer and the tests are untouched
and the 110 tests pass unchanged.

### Added

- Dark and light palettes. The application follows the Windows colour scheme
  by default; **Tools > Appearance** forces one and remembers the choice.
- A **Tools** menu holding what is used once rather than every time: album and
  favourite rebuild, content-duplicate listing, the three-inventory stability
  check, and a shortcut to the reports folder.
- `applesync/ui/assets/`: drop an `icon.png` in there and it becomes the
  window and taskbar icon. Nothing breaks if it is absent.

### Changed

- The window is now the flow: three numbered steps — inventory, copy, verify —
  instead of a row of seven equal buttons. The stability check no longer sits
  in the middle of the daily path.
- Device state is a chip in the header, next to the application name, visible
  whatever the window is doing; its tooltip carries the actionable message and
  the UDID.
- **Stop** sits next to the running phase rather than inside a step, since it
  stops whatever is running.
- The inventory plan is laid out as left-anchored columns and no longer wraps,
  so a long path can no longer shift the figures out of alignment.

### Fixed

- The plan text was rendering in the proportional UI font: a stylesheet
  `font-family` overrides `setFont()`, so the monospaced face is now requested
  from the stylesheet. Columns actually line up.
- Report headings were rendering at double size. Qt's Markdown importer sets a
  font-size *adjustment* on headings that takes precedence over any point size
  a stylesheet asks for; the heading format is now replaced outright.
- A long export path in the plan could overflow its panel and paint over the
  next step.

## [1.1.0] — 2026-08-06

Interface only. No change to the backup engine: the core, the device layer and
the tests are untouched, and the 110 tests pass unchanged.

### Added

- `applesync/ui/theme.py`: design tokens and the application stylesheet. One
  neutral family, one accent, three semantic states, a typographic scale, and
  explicit hover / pressed / focus / disabled states. No widget carries inline
  colours any more.
- Errors now appear inline in a dismissable strip; the modal dialog is opt-in
  and only carries the traceback.
- An empty state that explains what the inventory does, replaced by a summary
  of the last completed run once there is one.
- Tooltips on the tool buttons.

### Changed

- The two steps of the main flow are the only accented buttons; the tools sit
  behind a separator and the stop button is pushed to the right, instead of
  seven identical buttons in a row.
- Device state is a card with a status dot and a tinted background rather than
  a coloured left border; the UDID is selectable and monospaced.
- Counters, sizes, throughput, paths and fingerprints are monospaced, so
  digits stop shifting during a transfer.
- The plan panel scrolls internally, so a long conflict list no longer pushes
  the report off the window.
- The report renders with its own document stylesheet.

## [1.0.0] — 2026-08-06

First public release.

### Added

- Inventory with double enumeration: the device content is walked twice and
  the passes compared; any divergence stops everything and names the files
  that differ.
- Full coverage of the photo library: `/DCIM`, plus the iCloud zone
  (`PhotoData/CPLAssets`) and shared albums
  (`PhotoData/PhotoCloudSharingData`, filed under `_SharedAlbums/`).
- Incremental, idempotent copy; identity based on (path, size, mtime).
- Byte-exact resume after a screen lock or an unplug; no partial file can ever
  carry a final name.
- Verification by full disk re-read and SHA-256 comparison, with every
  discrepancy listed by name.
- Three destination layouts, frozen per destination: mirror, by date, archive
  (timestamp renaming from EXIF, `_LivePhotos/`, `_Duplicates/`).
- Album and favourite recovery from the device's Photos database, with
  defensive schema parsing.
- Content-based duplicate detection (report only, nothing deleted).
- Stability check: three successive inventories compared.
- JSONL journal and Markdown report per run, month x extension CSV breakdown
  per inventory.
- Deterministic device simulator with fault injection (silent truncation,
  disconnection, interrupted read, locked device) — 110 tests runnable
  without hardware.
- Update check at start-up, read-only and disableable.

### Security

- No write operation towards the device exists in the code: deleting or
  modifying data on the device is impossible by construction, not by policy.

[Unreleased]: https://github.com/eljokoGit/applesync/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/eljokoGit/applesync/releases/tag/v1.2.0
[1.1.0]: https://github.com/eljokoGit/applesync/releases/tag/v1.1.0
[1.0.0]: https://github.com/eljokoGit/applesync/releases/tag/v1.0.0
