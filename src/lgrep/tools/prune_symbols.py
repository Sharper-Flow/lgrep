"""Prune stale symbol-store index files.

Mirrors ``lgrep.tools.prune_orphans`` in shape, adapted to the
symbol-store layout: one ``index_<hash16>.json`` file per repo (see
``lgrep.storage.index_store.IndexStore``). Where prune_orphans handles
cache directories of LanceDB fragments, this module handles individual
JSON index files written atomically by ``IndexStore.save``.

Stale classification (3 reasons):
- ``repo_path_enoent``      — JSON parsed but ``repo_path`` directory is gone.
- ``unreadable_index_json`` — file missing or JSON unparseable.
- ``missing_repo_path_field`` — JSON valid but lacks ``repo_path``.

Reclamation classification (2 additional reasons, additive — the strict
``_INDEX_FILE_RE`` guard is unchanged):
- ``orphan_sidecar_json`` — ``index_<hash16>.meta.json`` with no matching
  index (pre-existing orphans, out-of-band index deletion, or the crash
  window between the two unlinks in ``delete_index``).
- ``stale_index_tmp``    — writer staging files older than the grace
  window. Both the legacy deterministic shape (``index_<hash16>.tmp``)
  and the writer-unique shape (``index_<hash16>[.meta].<pid>.<hex8>.tmp``)
  are covered. ``.index_<hash16>.lock`` files are NEVER swept: removing a
  lock inode another process waits on silently breaks mutual exclusion.

Sidecar-cheap classification: when an index has a key-verified sidecar,
``_classify`` takes ``repo_path`` from it WITHOUT parsing the index body.
Consequence (accepted, documented): a corrupt index behind a valid
sidecar is not classified ``unreadable_index_json``. Post-fix writers
cannot produce torn indexes (writer-unique temp + ``os.replace``), so
that state requires out-of-band corruption and self-heals on the next
re-index (``load()`` returns None and the index is rewritten). Legacy
corrupt indexes have no sidecars and still take the parse path. All
stale-reason semantics are IDENTICAL to the parse-only implementation
for sidecar-less indexes.

Non-local entries (``repo_path`` starting with ``github:``) are skipped
outright — they have no local filesystem path to staleness-check.

The grace window (``LGREP_PRUNE_MIN_AGE_S``, default 3600s) protects
only ``unreadable_index_json`` (mid-write indexer risk). The other two
reasons are unambiguous and bypass grace.

Guard parity with prune_orphans (C1):
- path-confinement at delete time (resolved path under resolved root)
- TOCTOU/symlink refusal at scan and delete time
- per-entry failure isolation (failures[] never aborts the batch)
- dry-run by default

KD7 note: this module does NOT add a writer/pruner mutex. The same
scan→unlink race exists today in ``prune_orphans`` (see
``prune_orphans.py`` delete loop); the proper fix covers both prune
paths in one change and is filed as a backlog item. Adding the guard
here alone would diverge from C1's parity requirement and leave the
sibling surface still exposed.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Literal

import structlog
from typing_extensions import TypedDict

from lgrep.storage.index_store import DEFAULT_SYMBOLS_DIR as _DEFAULT_SYMBOLS_DIR
from lgrep.storage.index_store import _read_sidecar_repo_path as _read_sidecar_repo_path
from lgrep.storage.index_store import _sidecar_for_index as _sidecar_for_index
from lgrep.tools._meta import make_meta

log = structlog.get_logger()

# Symbol-store index files are produced by
# ``lgrep.storage.index_store.IndexStore._index_path`` as
# ``index_<sha256[:16]>.json``. Anything that does not match this exact
# shape is not a canonical symbol index and must never be considered for
# pruning — this is the scan-time TOCTOU/shape guard that prevents
# unrelated files under the storage root from being unlinked.
_INDEX_FILE_RE = re.compile(r"^index_[0-9a-f]{16}\.json$")

# Reclamation shapes (additive; the index guard above is NOT widened).
# Sidecars are only reclaimable when orphaned (no matching index) or as
# companions of a stale index.
_ORPHAN_SIDECAR_RE = re.compile(r"^index_[0-9a-f]{16}\.meta\.json$")
# Writer staging files. The legacy deterministic shape
# (``index_<hash16>.tmp``) and the writer-unique shapes
# (``index_<hash16>[.meta].<pid>.<hex8>.tmp``) produced by
# ``index_store._unique_temp_path``.
_STALE_TMP_RE = re.compile(r"^index_[0-9a-f]{16}(?:\.meta)?(?:\.\d+\.[0-9a-f]{8})?\.tmp$")

# Non-local repo_path prefix. ``github:owner/name@ref`` keys have no
# local filesystem path to staleness-check; skip them outright.
_NONLOCAL_PREFIX = "github:"

StaleReason = Literal[
    "repo_path_enoent",
    "unreadable_index_json",
    "missing_repo_path_field",
    "orphan_sidecar_json",
    "stale_index_tmp",
]

# Grace window: ambiguous reasons can be produced by an indexer mid-write
# (atomic temp+rename leaves a window where the temp file is incomplete).
# Preserve very-recent unreadable indexes so a live indexing pass cannot
# be mid-pruned. Unambiguous reasons (``repo_path_enoent``,
# ``missing_repo_path_field``) bypass grace.
#
# Default: 1 hour. Override with LGREP_PRUNE_MIN_AGE_S (seconds).
# Reused from prune_orphans so operators have one knob for both stores.
_DEFAULT_GRACE_SECONDS = 3600
_GRACE_EXEMPT_REASONS: frozenset[StaleReason] = frozenset(
    {"repo_path_enoent", "missing_repo_path_field"}
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
    """Return True when ``path``'s mtime is within the grace window."""
    if grace_seconds <= 0:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) < grace_seconds


def _is_under(candidate: Path, root: Path) -> bool:
    """Return True when ``candidate`` is ``root`` or a descendant of it.

    Equivalent to ``candidate.is_relative_to(root)`` but resilient to
    non-existent paths and explicit about the intended semantics.
    """
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_storage_dir(storage_dir: Path | None = None) -> Path:
    """Resolve symbol storage directory, preferring explicit arg over env."""
    if storage_dir is not None:
        return Path(storage_dir)
    env = os.environ.get("LGREP_SYMBOLS_DIR")
    if env:
        return Path(env)
    return _DEFAULT_SYMBOLS_DIR


class StaleEntry(TypedDict):
    path: str
    reason: StaleReason
    bytes: int
    repo_path: str | None


class FailureEntry(TypedDict):
    path: str
    error: str


class PruneSymbolsReport(TypedDict):
    dry_run: bool
    files_examined: int
    stale_indexes: list[StaleEntry]
    skipped_active: list[str]
    deleted_files: int
    reclaimed_bytes: int
    failures: list[FailureEntry]
    _meta: dict


class _StaleIndexResults(list[StaleEntry]):
    """List-compatible stale results carrying the single-pass classifications."""

    def __init__(
        self,
        entries: list[StaleEntry],
        classified_repo_paths: list[str],
    ) -> None:
        super().__init__(entries)
        self.classified_repo_paths = classified_repo_paths


def _classify_by_repo_path(repo_path: str) -> tuple[StaleReason | None, str]:
    """Classify by ``repo_path`` alone — shared tail of the sidecar fast
    path and the index-parse path, so both assign reasons identically."""
    # Non-local entries have no on-disk path to staleness-check. Skip.
    if repo_path.startswith(_NONLOCAL_PREFIX):
        return None, repo_path

    try:
        if not Path(repo_path).is_dir():
            return "repo_path_enoent", repo_path
    except PermissionError:
        # Transient FS error (unmounted drive, EACCES) — preserve.
        return None, repo_path
    except OSError:
        # Same defensive stance as prune_orphans' PermissionError branch:
        # any OS-level failure checking repo_path existence is treated as
        # transient and the index is left alone.
        return None, repo_path

    return None, repo_path


def _classify(index_file: Path) -> tuple[StaleReason | None, str | None]:
    """Classify a single ``index_<hash16>.json`` file.

    Returns ``(reason, repo_path_from_json)``. ``reason=None`` means the
    file is healthy, non-local (github:), or transiently unreadable on
    the repo_path side; otherwise one of the stable ``StaleReason`` values.

    Sidecar fast path: a key-verified sidecar supplies ``repo_path``
    without parsing the index body (see module docstring for the accepted
    corrupt-index blind spot). The existence re-check guards the
    scan→classify race: if the index vanished after ``iterdir``, there is
    nothing to classify even if its sidecar lingers.
    """
    sidecar_repo_path = _read_sidecar_repo_path(index_file)
    if sidecar_repo_path is not None:
        try:
            if not index_file.is_file():
                return None, None
        except OSError:
            return None, None
        return _classify_by_repo_path(sidecar_repo_path)

    try:
        raw = index_file.read_text(encoding="utf-8")
    except OSError:
        # Missing file (already pruned by a concurrent run) or FS error.
        # Treat as already-gone; nothing to classify.
        return None, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "unreadable_index_json", None

    if not isinstance(data, dict):
        # Valid JSON but not an object — structurally invalid. Treat as
        # unreadable since we cannot extract a repo_path either way.
        return "unreadable_index_json", None

    repo_path = data.get("repo_path")
    if not repo_path or not isinstance(repo_path, str):
        return "missing_repo_path_field", None

    return _classify_by_repo_path(repo_path)


def _scan_indexes(
    root: Path,
    active_paths: set[str],
    grace_seconds: int,
) -> tuple[list[StaleEntry], list[str]]:
    """Classify index files once for stale detection and active accounting."""
    try:
        entries = list(root.iterdir())
    except OSError:
        # Unreadable storage root — treat it as empty rather than raise.
        return [], []

    stale: list[StaleEntry] = []
    classified_repo_paths: list[str] = []

    for child in entries:
        # Refuse anything that does not look like a canonical symbol
        # index file. The strict shape prevents unrelated files (and
        # symlink swaps into arbitrary paths) from ever being considered.
        if not _INDEX_FILE_RE.match(child.name):
            continue
        try:
            # Refuse symlinks: a symlinked index could point anywhere on
            # disk and is not produced by the symbol pipeline. Skipping
            # here prevents the execute path from ever resolving through
            # the link.
            if child.is_symlink():
                continue
            if not child.is_file():
                continue
        except (PermissionError, OSError):
            continue

        reason, repo_path = _classify(child)
        if (
            repo_path is not None
            and repo_path not in classified_repo_paths
        ):
            classified_repo_paths.append(repo_path)
        if reason is None:
            continue

        # Active set: an in-memory project that owns this repo_path. The
        # active process may have a live IndexStore handle even if the
        # on-disk repo_path is gone (e.g. mid-relocate); preserve it.
        if repo_path is not None and repo_path in active_paths:
            continue

        # Grace window: for ambiguous reasons a partial-write indexer
        # can legitimately produce the same on-disk shape. Preserve very
        # recent unreadable indexes so a live indexing pass cannot be
        # mid-pruned.
        if reason not in _GRACE_EXEMPT_REASONS and _mtime_recent(child, grace_seconds):
            continue

        try:
            size = child.stat().st_size
        except OSError:
            # If we cannot stat now, the file likely vanished between
            # classify and accounting — skip it; nothing to reclaim.
            continue

        stale.append(
            {
                "path": str(child),
                "reason": reason,
                "bytes": size,
                "repo_path": repo_path,
            }
        )
        # Companion sidecar: a stale index drags its sidecar with it, so the
        # execute path unlinks both and dry-run accounts for both. It rides
        # the index's verdict — no independent grace or active-set check.
        sidecar = _sidecar_for_index(child)
        try:
            if sidecar.is_file() and not sidecar.is_symlink():
                stale.append(
                    {
                        "path": str(sidecar),
                        "reason": reason,
                        "bytes": sidecar.stat().st_size,
                        "repo_path": repo_path,
                    }
                )
        except OSError:
            # Sidecar vanished between checks — nothing extra to reclaim.
            pass

    # Reclamation pass over the same directory listing. Shapes are additive
    # and strict; the index guard is unchanged above.
    names = {child.name for child in entries}
    for child in entries:
        if _ORPHAN_SIDECAR_RE.match(child.name):
            index_name = child.name[: -len(".meta.json")] + ".json"
            if index_name in names:
                continue  # legitimate sidecar with a live index
            try:
                if child.is_symlink() or not child.is_file():
                    continue
            except (PermissionError, OSError):
                continue
            # Active set: an in-memory project may be mid-recreate.
            orphan_repo_path = _read_sidecar_repo_path(
                child.with_name(index_name)
            )
            if orphan_repo_path is not None and orphan_repo_path in active_paths:
                continue
            # Grace: a fresh orphan may sit in the tiny delete_index crash
            # window (index unlinked, sidecar unlink imminent) or a
            # concurrent prune's unlink path — preserve it.
            if _mtime_recent(child, grace_seconds):
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            stale.append(
                {
                    "path": str(child),
                    "reason": "orphan_sidecar_json",
                    "bytes": size,
                    "repo_path": orphan_repo_path,
                }
            )
        elif _STALE_TMP_RE.match(child.name):
            try:
                if child.is_symlink() or not child.is_file():
                    continue
            except (PermissionError, OSError):
                continue
            # Grace is the ONLY protection for a live writer's staging file.
            # Writer-unique temps live only for the local write+replace
            # (seconds); anything older than the window is a crashed writer's
            # orphan.
            if _mtime_recent(child, grace_seconds):
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            stale.append(
                {
                    "path": str(child),
                    "reason": "stale_index_tmp",
                    "bytes": size,
                    "repo_path": None,
                }
            )

    return stale, classified_repo_paths


def find_stale_indexes(
    storage_dir: Path | None = None,
    active_set: list[str] | tuple[str, ...] = (),
    grace_seconds: int | None = None,
) -> list[StaleEntry]:
    """Scan the symbol store for stale ``index_*.json`` files.

    A file is stale iff its name matches ``index_<hash16>.json`` and one
    of the three ``StaleReason`` values applies. Non-local (``github:``)
    entries, transient FS errors, and currently-active in-memory projects
    are preserved.

    ``grace_seconds`` suppresses stale entries whose on-disk mtime is
    within the window — used to avoid racing an in-flight indexer.
    Defaults to the process-level default (see ``_grace_seconds``). Pass
    ``0`` to disable. ``repo_path_enoent`` and ``missing_repo_path_field``
    bypass the grace check (they are unambiguous).
    """
    root = _resolve_storage_dir(storage_dir)
    if not root.is_dir():
        return []

    if grace_seconds is None:
        grace_seconds = _grace_seconds()

    active_paths = {str(p) for p in active_set}
    stale, classified_repo_paths = _scan_indexes(root, active_paths, grace_seconds)
    return _StaleIndexResults(stale, classified_repo_paths)


def _count_index_shaped_files(root: Path) -> int:
    """Count immediate children matching the canonical index filename shape.

    Parity with ``prune_orphans._count_cache_shaped_dirs``: the reported
    ``files_examined`` reflects the number of index-candidate files the
    scan actually considered, not every unrelated child under the storage
    root.
    """
    total = 0
    if not root.is_dir():
        return 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for child in entries:
        if not _INDEX_FILE_RE.match(child.name):
            continue
        try:
            if child.is_symlink():
                continue
            if child.exists():
                total += 1
        except OSError:
            continue
    return total


def prune_symbols(
    dry_run: bool = True,
    storage_dir: Path | None = None,
    active_set: list[str] | tuple[str, ...] = (),
    grace_seconds: int | None = None,
) -> PruneSymbolsReport:
    """Report and optionally delete stale symbol-store index files.

    Dry-run by default. When ``dry_run=False``, each stale file is
    unlinked; per-entry failures are captured in ``failures[]`` and do
    not abort the batch. Symlinks are refused at delete time to guard
    against TOCTOU swap into an arbitrary target, and every unlink
    target must resolve inside the storage root (path-confinement guard).

    The dry-run report carries projected reclaim (sum of stale file
    ``stat().st_size``) in ``reclaimed_bytes`` so operators can preview
    savings before running with ``dry_run=False``.
    """
    t0 = time.monotonic()
    root = _resolve_storage_dir(storage_dir)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    active_paths = {str(p) for p in active_set}
    if grace_seconds is None:
        grace_seconds = _grace_seconds()
    # ``skipped_active`` lists active repo_paths that have an index file
    # on disk (whether stale or healthy). Active projects without an
    # on-disk index are not reported here because there is nothing to skip.
    stale = find_stale_indexes(root, active_set=active_set, grace_seconds=grace_seconds)
    classified_repo_paths = getattr(stale, "classified_repo_paths", [])
    skipped_active = [
        repo_path
        for repo_path in classified_repo_paths
        if repo_path in active_paths
    ]
    failures: list[FailureEntry] = []
    deleted_files = 0
    reclaimed_bytes = 0

    if dry_run:
        # Projected reclaim: dry-run surfaces savings so the operator can
        # decide whether to commit to --execute. The execute branch
        # re-sums from actually-deleted files.
        reclaimed_bytes = sum(entry["bytes"] for entry in stale)
    else:
        for entry in stale:
            entry_path = Path(entry["path"])
            # Orphan recheck (scan→unlink race): an "orphan" sidecar whose
            # index has been RECREATED since the scan now belongs to a live
            # index; deleting it would strip that index's sidecar. A cheap
            # stat per orphan closes the common case of the race (a recreate
            # landing mid-prune); the residual microsecond window degrades
            # to a list_repos() parse + backfill, never corruption.
            if entry["reason"] == "orphan_sidecar_json":
                index_name = entry_path.name[: -len(".meta.json")] + ".json"
                try:
                    if (entry_path.parent / index_name).exists():
                        continue
                except OSError:
                    continue
            # TOCTOU guard: a symlink could have been swapped in between
            # scan and delete. Refuse to follow symlinks out of the
            # storage tree — record as a failure instead.
            try:
                if entry_path.is_symlink():
                    log.warning(
                        "prune_refused_symlink",
                        path=str(entry_path),
                        store="symbols",
                    )
                    failures.append(
                        {
                            "path": str(entry_path),
                            "error": "refused: path is a symlink",
                        }
                    )
                    continue
            except OSError as exc:
                log.warning(
                    "prune_unlink_failed",
                    path=str(entry_path),
                    store="symbols",
                    error=str(exc),
                )
                failures.append({"path": str(entry_path), "error": str(exc)})
                continue

            # Path-confinement guard: resolve the candidate and ensure it
            # is strictly under the (resolved) storage root before any
            # destructive call. Protects against post-scan tampering of
            # the stale list or a poisoned repo_path field.
            try:
                resolved = entry_path.resolve()
            except OSError as exc:
                log.warning(
                    "prune_unlink_failed",
                    path=str(entry_path),
                    store="symbols",
                    error=str(exc),
                )
                failures.append({"path": str(entry_path), "error": str(exc)})
                continue
            if not _is_under(resolved, root_resolved):
                log.warning(
                    "prune_refused_outside_root",
                    path=str(entry_path),
                    store="symbols",
                )
                failures.append(
                    {
                        "path": str(entry_path),
                        "error": "refused: path is outside storage root",
                    }
                )
                continue

            try:
                entry_path.unlink()
                deleted_files += 1
                reclaimed_bytes += entry["bytes"]
            except OSError as exc:
                log.warning(
                    "prune_unlink_failed",
                    path=str(entry_path),
                    store="symbols",
                    error=str(exc),
                )
                failures.append({"path": str(entry_path), "error": str(exc)})

    return {
        "dry_run": dry_run,
        "files_examined": _count_index_shaped_files(root),
        "stale_indexes": stale,
        "skipped_active": skipped_active,
        "deleted_files": deleted_files,
        "reclaimed_bytes": reclaimed_bytes,
        "failures": failures,
        "_meta": make_meta(t0, __name__),
    }
