# Contributing to AppleSync

Thanks for your interest in the project. Contributions are welcome: bug
reports, suggestions, fixes, documentation.

## Reporting a bug

Open an [issue](https://github.com/eljokoGit/applesync/issues) using the "Bug
report" template. The most useful details:

- the AppleSync version (shown in the status bar), your Windows version, the
  iOS version and the device model;
- what you expected, and what happened instead;
- the full error message ("Details" button of the error dialog);
- if possible an excerpt of the journal, in
  `<destination>/.applesync/logs/run_*.jsonl`.

**Never attach a photo or the `manifest.sqlite3` file.** The journal and the
reports contain file names, which is almost always enough to diagnose — read
them over before posting.

## Development environment

```
git clone https://github.com/eljokoGit/applesync.git
cd applesync
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest tests -q
```

No device is required: all the logic is tested against the device simulator
(`applesync/device/simulator.py`), which produces a deterministic file tree
and can inject the faults that happen in the field.

Full UI walkthrough, still without hardware:

```
.venv\Scripts\python scripts\ui_smoke.py <screenshot_dir>
```

## Project rules

These are not negotiable — they define what this software is.

1. **No write towards the device.** The `DeviceSession` contract
   (`applesync/device/base.py`) exposes no write or delete method. A
   contribution adding one would be declined.
2. **Nothing in the destination is overwritten or deleted.** Conflicts resolve
   to a versioned name (`.~2`), never to a replacement.
3. **Fail loudly.** A partial result must never pass for a complete one: raise
   an explicit exception rather than returning an incomplete object, and list
   discrepancies by name rather than summarising them as a percentage.
4. **Every new behaviour is tested on the simulator**, including how it
   behaves when a fault is injected.
5. **Every long operation shows progress** (busy animation as soon as a phase
   starts, real percentage as soon as a counter exists).

## Style

- Python 3.12, standard library preferred.
- Code, comments, UI strings and documentation in English.
- Comments explain the *why*, not the *how*.

## Pull requests

- One intent per pull request.
- `pytest` must pass in full; CI checks it on Windows and Linux.
- Add an entry to `CHANGELOG.md` under "Unreleased".

## Cutting a release (maintainers)

1. Update `__version__` in `applesync/__init__.py` and the matching section of
   `CHANGELOG.md`.
2. Commit, then tag: `git tag v1.2.0 && git push --tags`.
3. The release workflow checks that the tag matches the version in the code,
   builds the packages and creates the GitHub release.
