"""Prune orphaned semantic cache directories."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Literal, TypedDict

from lgrep.storage import (
    CHUNKS_TABLE,
    DEFAULT_CACHE_DIR,
    get_project_db_path,
    read_project_meta,
)
from lgrep.tools._meta import make_meta

# Semantic-cache directory names are the first 12 hex chars of a
# SHA-256 of the absolute project path (see
# ``lgrep.storage._chunk_store.get_project_db_path``). Anything that
# does not match this shape is not a semantic cache directory and
# must never be considered for pruning.
_CACHE_DIR_NAME_RE = re.compile(r"^[0-9a-f]{12}$")

OrphanReason = Literal[
    "missing_meta",
    "unreadable_meta",
    "missing_chunks_lance",
    "project_path_enoent",
]

# Grace window for "ambiguous" orphan reasons. `unreadable_meta` and
# `missing_chunks_lance` can both be produced by an indexer mid-write,
# so we preserve caches that were modified recently. `missing_meta` and
# `project_path_enoent` are unambiguous and bypass the grace check.
#
# Default: 1 hour. Override with LGREP_PRUNE_MIN_AGE_S (seconds).
_DEFAULT_GRACE_SECONDS = 3600
_GRACE_EXEMPT_REASONS: frozenset[OrphanReason] = frozenset(
    {"missing_meta", "project_path_enoent"}
)


def _grace_seconds() -> int:
    raw = os.environ.get("LGREP_PRUNE_MIN_AGE_S")
    if raw is None:
        return _DEFAULT_GRACE_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return _DEFAULT_GRACE_SECONDS
    return max(0, parsed)


def _mtime_recent(path: Path, grace_seconds: int) -> bool:
    """Return True when `path`'s mtime is within the grace window."""
    if grace_seconds <= 0:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) < grace_seconds


def _is_under(candidate: Path, root: Path) -> bool:
    """Return True when `candidate` is `root` or a descendant of it.

    Equivalent to ``candidate.is_relative_to(root)`` but resilient to
    non-existent paths and explicit about the intended semantics.
    """
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


class OrphanEntry(TypedDict):
    path: str
    reason: OrphanReason
    bytes: int
    project_path: str | None


class FailureEntry(TypedDict):
    path: str
    error: str


class PruneReport(TypedDict):
    dry_run: bool
    dirs_examined: int
    orphans: list[OrphanEntry]
    skipped_active: list[str]
    deleted_dirs: int
    reclaimed_bytes: int
    failures: list[FailureEntry]
    _meta: dict


def _resolve_cache_dir(cache_dir: Path | None = None) -> Path:
    """Resolve cache directory, preferring explicit arg over env var."""
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get("LGREP_CACHE_DIR", DEFAULT_CACHE_DIR))


def _dir_size(path: Path) -> int:
    """Sum of root-path and descendant-path stat sizes (best-effort).

    Uses ``lstat`` rather than ``stat`` so symlinks do not transit into
    targets outside the cache tree. Orphan cache dirs should never
    contain symlinks, but this keeps the accounting honest if they do.
    """
    total = 0
    if not path.exists():
        return 0
    try:
        total += path.lstat().st_size
    except OSError:
        return 0
    for child in path.rglob("*"):
        try:
            total += child.lstat().st_size
        except OSError:
            continue
    return total


def _count_cache_shaped_dirs(root: Path) -> int:
    """Count immediate children whose name is a semantic-cache hash.

    Unlike a raw "is_dir" scan, this matches the shape filter used by
    ``find_orphans`` so the reported ``dirs_examined`` reflects the
    number of cache-candidate directories the scan actually considered,
    not every unrelated child under the cache root.
    """
    total = 0
    if not root.is_dir():
        return 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for child in entries:
        if not _CACHE_DIR_NAME_RE.match(child.name):
            continue
        try:
            if child.exists():
                total += 1
        except OSError:
            continue
    return total


def _active_cache_dirs(active_set: list[str]) -> dict[Path, str]:
    """Map each active project path to its hashed cache dir."""
    return {get_project_db_path(project): str(Path(project).resolve()) for project in active_set}


def _detect_orphan_reason(
    child: Path,
    meta: dict | None,
    chunks_dir: Path,
) -> tuple[OrphanReason | None, str | None]:
    """Classify a candidate cache dir.

    Returns (reason, project_path_from_meta). `reason=None` means the dir is
    healthy (or transiently unreadable on the project_path side); otherwise
    one of the stable OrphanReason enum values.
    """
    if meta is None:
        # `read_project_meta` returns None for both missing and unparseable
        # files. Distinguish by probing the file path directly.
        if not (child / "project_meta.json").exists():
            return "missing_meta", None
        return "unreadable_meta", None

    project_path = meta.get("project_path")
    if not chunks_dir.is_dir():
        return "missing_chunks_lance", project_path
    if not project_path:
        return "unreadable_meta", project_path
    try:
        if not Path(project_path).is_dir():
            return "project_path_enoent", project_path
    except PermissionError:
        # Transient FS error (unmounted drive, EACCES) — preserve cache.
        return None, project_path
    return None, project_path


def find_orphans(
    cache_dir: Path | None = None,
    active_set: list[str] | tuple[str, ...] = (),
    grace_seconds: int | None = None,
) -> list[OrphanEntry]:
    """Scan the semantic cache root for orphan directories.

    A directory is an orphan iff it looks like a semantic cache (has
    `chunks.lance/` or `project_meta.json`) and one of the four
    `OrphanReason` values applies. Transient FS errors and currently
    active in-memory projects are preserved.

    `grace_seconds` suppresses orphans whose on-disk mtime is within the
    window — used to avoid racing an in-flight indexer. Defaults to the
    process-level default (see ``_grace_seconds``). Pass ``0`` to
    disable. `missing_meta` and `project_path_enoent` bypass the grace
    check (they are unambiguous).
    """
    root = _resolve_cache_dir(cache_dir)
    if not root.is_dir():
        return []

    if grace_seconds is None:
        grace_seconds = _grace_seconds()

    active_dirs = _active_cache_dirs(list(active_set))
    orphans: list[OrphanEntry] = []

    try:
        entries = list(root.iterdir())
    except OSError:
        # Unreadable cache root — treat as empty rather than raise.
        return []

    for child in entries:
        # Refuse anything that does not look like a semantic cache
        # directory. Real cache dirs are 12-hex-char SHA-256 prefixes
        # produced by ``get_project_db_path``; filtering by this shape
        # protects against pruning unrelated subtrees under the cache
        # root and prevents symlink/TOCTOU swaps into arbitrary paths.
        if not _CACHE_DIR_NAME_RE.match(child.name):
            continue
        try:
            # Refuse symlinks: a symlinked cache directory could point
            # anywhere on disk and is not produced by the semantic
            # pipeline. Skipping here prevents the execute path from
            # ever resolving through the link.
            if child.is_symlink():
                continue
            is_dir = child.is_dir()
        except PermissionError:
            continue
        if not is_dir:
            continue
        if child in active_dirs:
            continue

        chunks_dir = child / f"{CHUNKS_TABLE}.lance"
        meta_path = child / "project_meta.json"
        if not chunks_dir.is_dir() and not meta_path.exists():
            # Not a semantic cache directory — skip (e.g. unrelated subtree).
            continue

        meta = read_project_meta(child)
        reason, project_path = _detect_orphan_reason(child, meta, chunks_dir)

        if reason is None:
            continue

        # Grace window: for ambiguous reasons a partial-write indexer can
        # legitimately produce the same on-disk shape. Preserve very
        # recent caches so a live indexing pass cannot be mid-pruned.
        if reason not in _GRACE_EXEMPT_REASONS and _mtime_recent(child, grace_seconds):
            continue

        orphans.append(
            {
                "path": str(child),
                "reason": reason,
                "bytes": _dir_size(child),
                "project_path": project_path,
            }
        )

    return orphans


def prune_orphans(
    dry_run: bool = True,
    cache_dir: Path | None = None,
    active_set: list[str] | tuple[str, ...] = (),
    grace_seconds: int | None = None,
) -> PruneReport:
    """Report and optionally delete orphan semantic cache directories.

    Dry-run by default. When `dry_run=False`, each orphan is deleted via
    `shutil.rmtree`; per-entry failures are captured in `failures[]` and
    do not abort the batch. Symlinks are refused at delete time to guard
    against TOCTOU swap into an arbitrary target, and every rmtree target
    must resolve inside the cache root (path-confinement guard).

    The dry-run report carries projected reclaim (sum of orphan bytes) in
    ``reclaimed_bytes`` so operators can preview savings before running
    with ``dry_run=False``.
    """
    t0 = time.monotonic()
    root = _resolve_cache_dir(cache_dir)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    active_dirs = _active_cache_dirs(list(active_set))
    # `skipped_active` lists projects that are in the active set AND have
    # a cache directory present on disk. Projects without an on-disk cache
    # are not reported here because there is nothing to skip.
    skipped_active = [
        project for cache_path, project in active_dirs.items() if cache_path.is_dir()
    ]
    orphans = find_orphans(root, active_set=active_set, grace_seconds=grace_seconds)
    failures: list[FailureEntry] = []
    deleted_dirs = 0
    reclaimed_bytes = 0

    if dry_run:
        # Projected reclaim: dry-run surfaces savings so the operator can
        # decide whether to commit to --execute. The execute branch
        # re-sums from actually-deleted entries.
        reclaimed_bytes = sum(orphan["bytes"] for orphan in orphans)
    else:
        for orphan in orphans:
            orphan_path = Path(orphan["path"])
            # TOCTOU guard: a symlink could have been swapped in between
            # scan and delete. Refuse to follow symlinks out of the cache
            # tree — record as a failure instead.
            try:
                if orphan_path.is_symlink():
                    failures.append(
                        {
                            "path": str(orphan_path),
                            "error": "refused: path is a symlink",
                        }
                    )
                    continue
            except OSError as exc:
                failures.append({"path": str(orphan_path), "error": str(exc)})
                continue

            # Path-confinement guard: resolve the candidate and ensure
            # it is strictly under the (resolved) cache root before any
            # destructive call. Protects against post-scan tampering of
            # the orphans list or a poisoned project_meta.json.
            try:
                resolved = orphan_path.resolve()
            except OSError as exc:
                failures.append({"path": str(orphan_path), "error": str(exc)})
                continue
            if not _is_under(resolved, root_resolved):
                failures.append(
                    {
                        "path": str(orphan_path),
                        "error": "refused: path is outside cache root",
                    }
                )
                continue

            try:
                shutil.rmtree(orphan_path)
                deleted_dirs += 1
                reclaimed_bytes += orphan["bytes"]
            except OSError as exc:
                failures.append({"path": str(orphan_path), "error": str(exc)})

    return {
        "dry_run": dry_run,
        "dirs_examined": _count_cache_shaped_dirs(root),
        "orphans": orphans,
        "skipped_active": skipped_active,
        "deleted_dirs": deleted_dirs,
        "reclaimed_bytes": reclaimed_bytes,
        "failures": failures,
        "_meta": make_meta(t0),
    }
