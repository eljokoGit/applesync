"""Execution engine: orchestrates inventory -> plan -> copy -> verification.

Used as-is by the UI (from a worker thread) and by the end-to-end tests. Two
deliberately separate steps:

1. `prepare()`: inventory (double enumeration) + plan. Nothing is written.
   The result is presented to the user for VALIDATION.
2. `execute(prepared)`: copy + verification + report. Never starts without a
   `PreparedRun` produced by `prepare()`.

Any inventory error stops everything: never a copy on a doubtful inventory. A
disconnection during the copy interrupts the run, leaving a state that resumes
identically on the next launch (.part files).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from applesync.core.copier import CopyCancelled, copy_file
from applesync.core.inventory import Inventory, take_inventory
from applesync.core.journal import Journal, new_run_id
from applesync.core.layout import Layout, LayoutLockedError, MirrorLayout
from applesync.core.manifest import Manifest
from applesync.core.planner import SyncPlan, build_plan
from applesync.core.report import RunReport
from applesync.core.verifier import verify_against_inventory
from applesync.device.base import (
    DeviceBackend,
    DeviceDisconnectedError,
    DeviceError,
    RemoteFile,
)

# Run outcomes, also used as manifest run statuses.
COMPLETED = "completed"
INTERRUPTED = "interrupted"
FAILED = "failed"


@dataclass
class PreparedRun:
    """Phase 1 result, to be shown for validation before any copy."""

    inventory: Inventory
    plan: SyncPlan
    device_label: str
    udid: str
    breakdown_csv: Optional[Path] = None   # month x extension breakdown


@dataclass
class ProgressSnapshot:
    """Live copy state for the UI (file, counter, volume, throughput, ETA)."""

    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    bytes_per_s: float = 0.0

    @property
    def eta_s(self) -> Optional[float]:
        if self.bytes_per_s <= 0:
            return None
        return (self.bytes_total - self.bytes_done) / self.bytes_per_s


ProgressListener = Callable[[ProgressSnapshot], None]
PhaseListener = Callable[[str], None]


class SyncEngine:
    def __init__(self, backend: DeviceBackend, dest_root: Path,
                 layout: Optional[Layout] = None):
        self.backend = backend
        self.dest_root = Path(dest_root)
        self.layout = layout or MirrorLayout()

    # ------------------------------------------------------------------ phase 1
    def prepare(
        self,
        udid: str,
        phase_cb: Optional[PhaseListener] = None,
        inventory_progress: Optional[Callable[[int, str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> PreparedRun:
        """Verified inventory + plan. Raises at the slightest doubt.

        Writes nothing into the backup mirror; it does export the month x
        extension breakdown of the inventory as CSV under
        `.applesync/reports/` (useful when only taking an inventory)."""
        if phase_cb:
            phase_cb("Connecting to the device…")
        session = self.backend.connect(udid)
        try:
            info = session.device_info()
            label = f"{info.name} ({info.model}, iOS {info.ios_version}, {info.udid})"
            if phase_cb:
                phase_cb("Inventory (double enumeration)…")
            inventory = take_inventory(
                session, progress_cb=inventory_progress, cancel=cancel
            )
        finally:
            session.close()

        if phase_cb:
            phase_cb("Building the plan…")
        with Manifest(self.dest_root) as manifest:
            locked = manifest.locked_layout()
            if locked is not None and locked != self.layout.id:
                raise LayoutLockedError(locked, self.layout.id)
            plan = build_plan(inventory, manifest, self.dest_root, self.layout)

        from applesync.core.analyze import write_breakdown_csv

        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(inventory.taken_at))
        csv_path = write_breakdown_csv(
            inventory,
            self.dest_root / RunReport.REPORTS_RELPATH / f"inventory_{ts}.csv",
        )
        return PreparedRun(
            inventory=inventory, plan=plan, device_label=label, udid=udid,
            breakdown_csv=csv_path,
        )

    # ------------------------------------------------------------------ placement
    def _finalize_placement(self, remote, result, manifest, journal, report,
                            ts_map, run_assigned) -> str:
        """Decide and apply the final location of a copied file.

        A single atomic move covers both the final dating (EXIF, when the
        layout asks for it) and the filing of content duplicates. Collisions
        resolve to .~N against both the disk AND the targets already promised
        in this run. Never an overwrite."""
        from applesync.core.layout import SHARED_DIRNAME
        from applesync.core.planner import versioned_target

        provisional = result.local_relpath
        final_rel = provisional

        if self.layout.finalize_dating:
            final_rel = self._dated_rel(remote, provisional, ts_map)

        prior = None
        is_shared = final_rel.startswith(SHARED_DIRNAME + "/")
        if self.layout.duplicates_dir is not None and not is_shared:
            # Shared albums keep their per-album structure: content identical
            # to the library is not a "duplicate" to file away there, it is
            # the same photo seen through the share.
            prior = manifest.lookup_by_content(result.sha256, remote.size)
            if prior is not None:
                final_rel = f"{self.layout.duplicates_dir}/{final_rel}"

        if final_rel != provisional:
            candidate = final_rel
            if (self.dest_root / candidate).exists() or candidate in run_assigned:
                candidate = versioned_target(self.dest_root, final_rel, run_assigned)
            dst = self.dest_root / candidate
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.dest_root / provisional, dst)
            final_rel = candidate
        run_assigned.add(final_rel)

        if prior is not None:
            journal.event(
                "duplicate_filed",
                path=remote.path,
                filed_under=final_rel,
                identical_to=prior.local_path,
                sha256=result.sha256,
            )
            report.duplicates_routed.append(
                (remote.path, final_rel, prior.local_path)
            )
        elif final_rel != provisional:
            journal.event("placed", path=remote.path, target=final_rel)

        result.local_relpath = final_rel
        return final_rel

    def _dated_rel(self, remote: RemoteFile, provisional: str, ts_map: dict) -> str:
        """Final dated target: EXIF of the local photo, mtime as fallback;
        Live Photo MOVs and AAE sidecars inherit their photo's date."""
        from applesync.core.exifdate import exif_timestamp
        from applesync.core.layout import PHOTO_EXTENSIONS, _dir_stem, shared_target

        path = remote.path
        part = shared_target(path)
        if part is not None:
            return part          # shared albums: apart, structure preserved
        ext = path.rsplit(".", 1)[-1].upper() if "." in path else ""
        layout = self.layout

        if ext in PHOTO_EXTENSIONS:
            ts = exif_timestamp(self.dest_root / provisional) or remote.mtime
            ts_map[_dir_stem(path)] = ts
            return layout.dated_target(remote, ts, as_live=False)

        pair = layout.paired_photo(remote) if ext in ("MOV", "AAE") else None
        if pair is not None:
            ts = ts_map.get(_dir_stem(pair.path), pair.mtime)
            return layout.dated_target(remote, ts, as_live=(ext == "MOV"))

        return layout.dated_target(remote, remote.mtime, as_live=False)

    # ------------------------------------------------------------------ phase 2
    def execute(
        self,
        prepared: PreparedRun,
        progress: Optional[ProgressListener] = None,
        phase_cb: Optional[PhaseListener] = None,
        cancel: Optional[Callable[[], bool]] = None,
        deep_verify: bool = True,
        verify_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> RunReport:
        """Copy the validated plan, verify the destination, write report+journal.

        Returns a RunReport whatever the outcome (completed, interrupted,
        failed) — status and error are in it. Only raises on an internal bug.
        """
        run_id = new_run_id()
        report = RunReport(run_id=run_id, device_label=prepared.device_label)
        report.inventory = prepared.inventory
        report.plan = prepared.plan

        journal = Journal(self.dest_root, run_id)
        manifest = Manifest(self.dest_root)
        manifest.set_meta("layout", self.layout.id)   # freeze the layout
        manifest.start_run(run_id, prepared.inventory.device_udid)
        journal.event(
            "run_started",
            device=prepared.device_label,
            inventory_files=prepared.inventory.count,
            inventory_bytes=prepared.inventory.total_bytes,
            fingerprint=prepared.inventory.fingerprint(),
            to_copy=len(prepared.plan.to_copy),
            conflicts=len(prepared.plan.conflicts),
            missing_on_device=len(prepared.plan.missing_on_device),
        )
        manifest.update_run(
            run_id,
            inventory_count=prepared.inventory.count,
            inventory_bytes=prepared.inventory.total_bytes,
        )

        transfers = prepared.plan.files_to_transfer
        if self.layout.finalize_dating:
            # .AAE sidecars follow their photo's date, so they go last, once
            # every photo has been dated (EXIF read at copy time).
            transfers = sorted(
                transfers,
                key=lambda t: (t[0].path.upper().endswith(".AAE"), t[0].path),
            )
        ts_map: dict[tuple[str, str], int] = {}   # (folder, stem) -> chosen date
        run_assigned: set[str] = set()            # final targets promised this run
        snap = ProgressSnapshot(
            files_total=len(transfers),
            bytes_total=sum(f.size for f, _ in transfers),
        )
        window_start = time.time()
        window_bytes = 0

        session = None
        status = COMPLETED
        try:
            # --- adoption: files already on disk, never copied again --------
            if prepared.plan.to_adopt:
                if phase_cb:
                    phase_cb(
                        f"Adopting {len(prepared.plan.to_adopt)} files already present…"
                    )
                from applesync.core.verifier import _sha256_of

                for adopt_i, f in enumerate(prepared.plan.to_adopt, 1):
                    if cancel and cancel():
                        raise CopyCancelled("adoption interrupted")
                    rel = prepared.plan.targets[f.path]
                    if verify_progress:
                        verify_progress(adopt_i, len(prepared.plan.to_adopt), rel)
                    sha = _sha256_of(self.dest_root / rel)
                    manifest.record_file(
                        f, sha, rel, run_id, prepared.inventory.device_udid
                    )
                    journal.event("file_adopted", path=f.path, sha256=sha)

            # --- copy --------------------------------------------------------
            if transfers:
                if phase_cb:
                    phase_cb("Connecting for the copy…")
                session = self.backend.connect(prepared.udid)
                if phase_cb:
                    phase_cb("Copying…")

            for remote, target_rel in transfers:
                snap.current_file = remote.path

                def file_progress(done: int, total: int, _remote=remote) -> None:
                    nonlocal window_start, window_bytes
                    base = snap.bytes_done
                    now = time.time()
                    elapsed = now - window_start
                    if elapsed >= 1.0:
                        snap.bytes_per_s = ((base + done) - window_bytes) / elapsed
                        window_start = now
                        window_bytes = base + done
                    if progress:
                        current = ProgressSnapshot(
                            current_file=_remote.path,
                            files_done=snap.files_done,
                            files_total=snap.files_total,
                            bytes_done=base + done,
                            bytes_total=snap.bytes_total,
                            bytes_per_s=snap.bytes_per_s,
                        )
                        progress(current)

                if self.layout.finalize_dating:
                    # Orphan staging file (interruption between copy and
                    # placement): never recorded in the manifest, so start over.
                    stale = self.dest_root / target_rel
                    if stale.exists():
                        journal.event("orphan_staging_purged", target=target_rel)
                        stale.unlink()

                try:
                    result = copy_file(
                        session,
                        remote,
                        self.dest_root,
                        target_rel,
                        journal,
                        cancel=cancel,
                        progress_cb=file_progress,
                    )
                except DeviceDisconnectedError as e:
                    journal.event("disconnected", path=remote.path, error=str(e))
                    report.failures.append((remote.path, f"disconnected: {e}"))
                    status = INTERRUPTED
                    report.error = (
                        f"Session dropped during {remote.path} (screen locked or "
                        f"cable unplugged). Files already copied are safe; the "
                        f"current file will resume byte-exactly on the next run."
                    )
                    break
                except DeviceError as e:
                    journal.event("file_failed", path=remote.path, error=str(e))
                    report.failures.append((remote.path, str(e)))
                    # Error limited to one file: carry on. It stays visible in
                    # the report and verification will flag it again.
                    continue

                final_rel = self._finalize_placement(
                    remote, result, manifest, journal, report, ts_map, run_assigned
                )
                report.copies.append(result)
                manifest.record_file(
                    remote,
                    result.sha256,
                    final_rel,
                    run_id,
                    prepared.inventory.device_udid,
                )
                snap.files_done += 1
                snap.bytes_done += remote.size
                if progress:
                    progress(snap)

        except CopyCancelled:
            status = INTERRUPTED
            report.error = (
                "Interruption requested. Files already copied are safe; the "
                "current file will resume byte-exactly."
            )
            journal.event("user_interrupted")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

        # --- verification ----------------------------------------------------
        # Verify what is supposed to be on disk: the whole inventory if the run
        # went to the end, otherwise only what was copied or adopted this time
        # (.part files never count as copied).
        try:
            if status == COMPLETED:
                if phase_cb:
                    phase_cb("Full verification of the destination…")
                to_check: list[RemoteFile] = list(prepared.inventory.files)
            else:
                if phase_cb:
                    phase_cb("Verifying the files copied before the interruption…")
                copied_paths = {c.remote.path for c in report.copies}
                adopted = {f.path for f in prepared.plan.to_adopt}
                to_check = [
                    f for f in prepared.inventory.files
                    if f.path in copied_paths or f.path in adopted
                ]
            report.verification = verify_against_inventory(
                to_check,
                manifest,
                self.dest_root,
                deep_hash=deep_verify,
                progress_cb=verify_progress,
                cancel=None,   # a run's verification is not interruptible
            )
            journal.event(
                "verification",
                checked=report.verification.checked_count,
                hashed=report.verification.hashed_count,
                conforming=report.verification.ok_count,
                discrepancies=[
                    {"source": d.source_path, "kind": d.kind, "detail": d.detail}
                    for d in report.verification.discrepancies
                ],
            )
            if status == COMPLETED and not report.verification.ok:
                status = FAILED
                report.error = (
                    f"Verification found discrepancies on "
                    f"{len(report.verification.discrepancies)} file(s) — see the "
                    f"list by name. DO NOT delete the originals."
                )
            if status == COMPLETED and report.failures:
                status = FAILED
                report.error = (
                    f"{len(report.failures)} file(s) failed to copy — see the "
                    f"list. DO NOT delete the originals."
                )
        except Exception as e:  # verification must never pass in silence
            status = FAILED
            report.error = f"Verification impossible: {e}"
            journal.event("verification_impossible", error=str(e))

        report.status = status
        report.finished_at = time.time()
        journal.event(
            "run_finished",
            status=status,
            copies=len(report.copies),
            failures=len(report.failures),
        )
        report_path = report.save(self.dest_root)
        manifest.update_run(
            run_id,
            finished_at=report.finished_at,
            status=status,
            copied_count=len(report.copies),
            copied_bytes=sum(c.remote.size for c in report.copies),
            report_path=str(report_path),
        )
        manifest.close()
        journal.close()
        return report
