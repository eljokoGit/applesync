# Changelog

Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [semantic versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/eljokoGit/applesync/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/eljokoGit/applesync/releases/tag/v1.0.0
