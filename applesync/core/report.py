"""Final run report, readable and naming every exception.

One report per run, in Markdown, written to `.applesync/reports/`.
Discrepancies and incidents are listed BY NAME — never a bare percentage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from applesync.core.copier import CopyResult
from applesync.core.inventory import Inventory
from applesync.core.planner import SyncPlan
from applesync.core.verifier import VerificationReport


def fmt_bytes(n: int) -> str:
    """Human-readable size (binary units) — shared with the UI."""
    return _fmt_bytes(n)


def fmt_duration(s: float) -> str:
    return _fmt_duration(s)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _fmt_duration(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else (f"{m}m{sec:02d}s" if m else f"{sec}s")


@dataclass
class RunReport:
    run_id: str
    device_label: str
    status: str = "running"           # completed | interrupted | failed
    inventory: Optional[Inventory] = None
    plan: Optional[SyncPlan] = None
    copies: list[CopyResult] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)   # (path, error)
    # (device source, filed under, identical to) — "archive" layout
    duplicates_routed: list[tuple[str, str, str]] = field(default_factory=list)
    verification: Optional[VerificationReport] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    REPORTS_RELPATH = Path(".applesync") / "reports"

    def to_markdown(self) -> str:
        lines: list[str] = []
        add = lines.append
        add(f"# Synchronisation report — {self.run_id}")
        add("")
        add(f"- **Device**: {self.device_label}")
        add(f"- **Started**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.started_at))}")
        if self.finished_at:
            add(f"- **Finished**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.finished_at))}"
                f" (duration {_fmt_duration(self.finished_at - self.started_at)})")
        add(f"- **Status**: **{self.status.upper()}**")
        if self.error:
            add(f"- **Error**: {self.error}")
        add("")

        if self.inventory is not None:
            inv = self.inventory
            add("## Source inventory")
            add("")
            add(f"- {inv.count} files, {_fmt_bytes(inv.total_bytes)}")
            add(f"- Double enumeration: {'yes — matching' if inv.double_checked else 'NO'}")
            add(f"- Fingerprint: `{inv.fingerprint()}`")
            add(f"- Duration: {_fmt_duration(inv.duration_s)}")
            add("")

        if self.plan is not None:
            p = self.plan
            add("## Plan")
            add("")
            add(f"- To copy: {len(p.to_copy)} ({_fmt_bytes(sum(f.size for f in p.to_copy))})")
            add(f"- Already synchronised: {len(p.already_synced)}")
            add(f"- Adopted (already on disk, re-recorded): {len(p.to_adopt)}")
            add(f"- Conflicts (copied under a versioned name): {len(p.conflicts)}")
            for c in p.conflicts:
                add(f"  - `{c.remote.path}` -> `{c.versioned_path}` — {c.reason}")
            add(f"- Gone from the device (kept on the PC): {len(p.missing_on_device)}")
            for e in p.missing_on_device:
                add(f"  - `{e.source_path}` (copied on "
                    f"{time.strftime('%Y-%m-%d', time.localtime(e.synced_at))}, "
                    f"local: `{e.local_path}`)")
            add("")

        add("## Copies")
        add("")
        copied_bytes = sum(c.remote.size for c in self.copies)
        resumed = [c for c in self.copies if c.resumed_from > 0]
        add(f"- Files copied: {len(self.copies)} ({_fmt_bytes(copied_bytes)})")
        if resumed:
            add(f"- Of which resumed mid-file: {len(resumed)}")
            for c in resumed:
                add(f"  - `{c.remote.path}` resumed at byte {c.resumed_from}")
        if self.duplicates_routed:
            add(f"- Content duplicates filed under `_Duplicates/`: "
                f"{len(self.duplicates_routed)}")
            for src, dup_rel, original in self.duplicates_routed:
                add(f"  - `{src}` -> `{dup_rel}` (identical to `{original}`)")
        if self.failures:
            add(f"- **Failures: {len(self.failures)}**")
            for path, err in self.failures:
                add(f"  - `{path}`: {err}")
        add("")

        add("## Destination verification")
        add("")
        if self.verification is None:
            add("- **NOT PERFORMED** — this run certifies nothing.")
        else:
            v = self.verification
            add(f"- Files checked: {v.checked_count}")
            add(f"- Re-read and hashed: {v.hashed_count}")
            add(f"- Conforming: {v.ok_count}")
            if v.ok:
                add("- **No discrepancy.**")
            else:
                add(f"- **DISCREPANCIES: {len(v.discrepancies)}** — full list:")
                for d in v.discrepancies:
                    add(f"  - `{d.source_path}` [{d.kind}] {d.detail}")
        add("")
        return "\n".join(lines)

    def save(self, dest_root: Path) -> Path:
        out_dir = Path(dest_root) / self.REPORTS_RELPATH
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"report_{self.run_id}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path
