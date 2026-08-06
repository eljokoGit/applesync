"""Sync plan: the delta between the source inventory and the manifest.

Every inventory file lands in one of these buckets:
- `already_synced`: identity (path, size, mtime) already in the manifest.
- `to_adopt`: not in the manifest, but a local file exists at the target path
  with the same size AND the same mtime (left by an earlier copy). Typical
  case: a lost manifest or a pre-filled destination. We hash the local file
  and adopt it instead of copying again — a stronger criterion than the name.
- `conflicts`: a local file exists at the target path but does NOT match
  (different size or mtime). A local file is never replaced: the new version
  goes to a versioned name (IMG_0001.HEIC -> IMG_0001.~2.HEIC).
- `to_copy`: everything else — to be fetched.

And on the disappearance side:
- `missing_on_device`: manifest entries absent from the inventory (deleted on
  the iPhone). The local file stays; the report names it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from applesync.core.inventory import Inventory
from applesync.core.layout import Layout, MirrorLayout
from applesync.core.manifest import Manifest, ManifestEntry
from applesync.device.base import RemoteFile


@dataclass(frozen=True)
class Conflict:
    remote: RemoteFile
    local_path: str          # relative local path already taken
    versioned_path: str      # relative local path the new version will use
    reason: str


@dataclass
class SyncPlan:
    to_copy: list[RemoteFile] = field(default_factory=list)
    to_adopt: list[RemoteFile] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    already_synced: list[RemoteFile] = field(default_factory=list)
    missing_on_device: list[ManifestEntry] = field(default_factory=list)
    targets: dict[str, str] = field(default_factory=dict)   # source path -> local target

    @property
    def bytes_to_copy(self) -> int:
        return sum(f.size for f in self.to_copy) + sum(
            c.remote.size for c in self.conflicts
        )

    @property
    def files_to_transfer(self) -> list[tuple[RemoteFile, str]]:
        """(source file, relative local target) for the copy phase."""
        out = [(f, self.targets[f.path]) for f in self.to_copy]
        out.extend((c.remote, c.versioned_path) for c in self.conflicts)
        return out


def local_target(source_path: str) -> str:
    """Relative local target in the mirror layout."""
    return str(PurePosixPath(source_path))


def staging_target(f: RemoteFile) -> str:
    """Staging location for a file awaiting its final dating.

    Deterministic per source path (so a resume finds its .part again from one
    run to the next) and confined under .applesync/staging/: it can never
    collide with a final target."""
    import hashlib

    digest = hashlib.sha1(f.path.encode("utf-8")).hexdigest()[:24]
    ext = f.path.rsplit(".", 1)[-1].lower() if "." in f.path else "bin"
    return f".applesync/staging/{digest}.{ext}"


def versioned_target(dest_root: Path, target_rel: str, taken: set[str]) -> str:
    """First free versioned path (neither on disk nor already promised by the
    plan): IMG_0001.HEIC -> IMG_0001.~2.HEIC, .~3…"""
    p = PurePosixPath(target_rel)
    stem, suffix = p.stem, p.suffix
    n = 2
    while True:
        candidate = str(p.parent / f"{stem}.~{n}{suffix}")
        if candidate not in taken and not (dest_root / candidate).exists():
            return candidate
        n += 1


def build_plan(
    inventory: Inventory,
    manifest: Manifest,
    dest_root: Path,
    layout: Optional[Layout] = None,
) -> SyncPlan:
    layout = layout or MirrorLayout()
    layout.begin(inventory.files)
    plan = SyncPlan()
    dest_root = Path(dest_root)
    inventory_paths = set()
    assigned: set[str] = set()   # targets promised by this plan (anti-collision)

    for f in inventory.files:
        inventory_paths.add(f.path)
        entry = manifest.lookup(f.identity)
        if entry is not None:
            plan.already_synced.append(f)
            plan.targets[f.path] = entry.local_path
            continue

        if layout.finalize_dating:
            # The final target will be decided after the copy (EXIF read
            # locally). The plan only assigns a STAGING location, deterministic
            # per source file (byte-exact resume preserved), in a space that
            # can never collide with final targets.
            plan.to_copy.append(f)
            plan.targets[f.path] = staging_target(f)
            continue

        target_rel = layout.target_for(f)
        target_abs = dest_root / target_rel
        if target_abs.exists():
            st = target_abs.stat()
            if st.st_size == f.size and int(st.st_mtime) == f.mtime:
                plan.to_adopt.append(f)
                plan.targets[f.path] = target_rel
                assigned.add(target_rel)
            else:
                versioned = versioned_target(dest_root, target_rel, assigned)
                plan.conflicts.append(
                    Conflict(
                        remote=f,
                        local_path=target_rel,
                        versioned_path=versioned,
                        reason=(
                            f"local file present with different size/mtime "
                            f"(local: {st.st_size} B, mtime {int(st.st_mtime)}; "
                            f"device: {f.size} B, mtime {f.mtime})"
                        ),
                    )
                )
                plan.targets[f.path] = versioned
                assigned.add(versioned)
        elif target_rel in assigned:
            # Two different source files aim at the same target (possible in
            # the date layout: same name, same month). The second is versioned
            # right in the plan — never an overwrite, never a late failure.
            versioned = versioned_target(dest_root, target_rel, assigned)
            plan.conflicts.append(
                Conflict(
                    remote=f,
                    local_path=target_rel,
                    versioned_path=versioned,
                    reason="name collision inside the plan (another source "
                           "file aims at the same target)",
                )
            )
            plan.targets[f.path] = versioned
            assigned.add(versioned)
        else:
            plan.to_copy.append(f)
            plan.targets[f.path] = target_rel
            assigned.add(target_rel)

    # Deletions on the device: in the manifest but no longer in the inventory.
    seen_identities = {f.identity for f in inventory.files}
    for entry in manifest.all_entries():
        if entry.source_path not in inventory_paths:
            plan.missing_on_device.append(entry)
        elif entry.identity not in seen_identities:
            # The path still exists but with another identity: the older
            # version is gone from the phone. Reported as well.
            plan.missing_on_device.append(entry)

    return plan
