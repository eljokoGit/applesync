"""Duplicate detection by CONTENT (SHA-256), not by name.

The manifest stores the SHA-256 of every copied or adopted file: two entries
with the same hash (and the same size, as a double check) are content
duplicates, whatever their name or folder.

Output: a report listing every group by name — the application NEVER deletes
anything by itself, neither on the device (impossible by construction) nor in
the destination. Any cleanup belongs to the user, list in hand.

Only meaningful after a synchronisation (hashes appear at copy time); no
device required.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from applesync.core.manifest import Manifest, ManifestEntry


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    size: int
    entries: tuple[ManifestEntry, ...]     # >= 2, sorted by sync date

    @property
    def wasted_bytes(self) -> int:
        """Bytes reclaimable if only one copy were kept."""
        return self.size * (len(self.entries) - 1)


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    scanned_count: int = 0

    @property
    def duplicate_count(self) -> int:
        return sum(len(g.entries) - 1 for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)

    def to_markdown(self) -> str:
        from applesync.core.report import fmt_bytes

        lines = ["# Content duplicates (identical SHA-256)", ""]
        lines.append(f"- Files examined (in the manifest): {self.scanned_count}")
        if not self.groups:
            lines.append("- **No content duplicate.**")
            return "\n".join(lines)
        lines.append(f"- Duplicate groups: {len(self.groups)}")
        lines.append(
            f"- Surplus copies: {self.duplicate_count} "
            f"({fmt_bytes(self.wasted_bytes)} reclaimable)"
        )
        lines.append("")
        lines.append(
            "The application deletes nothing: this list is for you to decide. "
            "Paths are relative to the destination."
        )
        lines.append("")
        for g in self.groups:
            lines.append(
                f"## {fmt_bytes(g.size)} x {len(g.entries)} — `{g.sha256[:16]}…`"
            )
            for e in g.entries:
                when = time.strftime("%Y-%m-%d", time.localtime(e.synced_at))
                lines.append(
                    f"- `{e.local_path}` (device source: `{e.source_path}`, "
                    f"synchronised on {when})"
                )
            lines.append("")
        return "\n".join(lines)


def find_duplicates(manifest: Manifest) -> DuplicateReport:
    """Groups of manifest entries sharing (sha256, size)."""
    by_hash: dict[tuple[str, int], list[ManifestEntry]] = defaultdict(list)
    entries = manifest.all_entries()
    for e in entries:
        by_hash[(e.sha256, e.size)].append(e)

    groups = [
        DuplicateGroup(
            sha256=key[0],
            size=key[1],
            entries=tuple(sorted(v, key=lambda e: (e.synced_at, e.local_path))),
        )
        for key, v in by_hash.items()
        if len(v) > 1
    ]
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return DuplicateReport(groups=groups, scanned_count=len(entries))
