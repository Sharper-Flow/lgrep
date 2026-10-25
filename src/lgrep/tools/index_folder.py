"""lgrep_index_symbols_folder tool implementation.

Indexes all symbols and candidate Python/Go occurrences in a local folder and
persists to IndexStore.
"""

from __future__ import annotations

import hashlib
import time
from itertools import count, groupby
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.exceptions import OperationCancelled
from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec
from lgrep.storage._occurrences import STORAGE_VERSION
from lgrep.storage.index_store import CodeIndex, IndexStore, _version_tuple
from lgrep.tools._meta import error_response

if TYPE_CHECKING:
    import threading

log = structlog.get_logger()

_INDEX_VERSION = "2.3"
# Minimum index version whose occurrence data is complete: 2.1 introduced
# Python occurrences, 2.2 added Go occurrences, 2.3 corrects Go extraction
# content (grouped type specs, type aliases, import-kind qualifiers).
# Older indexes must be re-parsed before callers can trust occurrence results.
_MIN_VERSION_FOR_OCCURRENCES = (2, 3)

_extractor = SymbolExtractor()


def _is_test_file_heuristic(rel_path: str) -> bool:
    """Return True if a relative path looks like a test file.

    This is an intentionally narrow heuristic: it does not claim to find all
    tests or avoid all non-test files. It is only used for the production-first
    / tests-only / include-tests filter in reference lookup.
    """
    parts = Path(rel_path).parts
    if any(part == "tests" or part.startswith("test_") or part.endswith("_test") for part in parts):
        return True
    name = Path(rel_path).name
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _occurrence_to_dict(occ, is_test_file: bool) -> dict:
    """Serialize an Occurrence for JSON storage."""
    return {
        "id": occ.id,
        "name": occ.name,
        "file_path": occ.file_path,
        "start_byte": occ.start_byte,
        "end_byte": occ.end_byte,
        "line_number": occ.line_number,
        "line_text": occ.line_text,
        "kind": occ.kind,
        "enclosing_symbol_id": occ.enclosing_symbol_id,
        "is_test_file": is_test_file,
    }


class _IndexMerge:
    """Symbols and occurrence buckets under merge, indexed by source file.

    Removing a file touches only the buckets that hold its rows, so a refresh
    does not rescan the whole index once per changed file. The merged result,
    including key and row order, is what filtering every symbol and bucket by
    file_path would produce. A bucket's first removal filters it into a list
    the merge owns. A bucket touched by a second removal is split once into
    runs of consecutive rows from one file, and later removals drop only their
    runs. An emptied bucket loses its position. Lists of the loaded index,
    which the index cache may share, are never mutated; an untouched bucket
    keeps its original list.
    """

    def __init__(self, symbols: dict[str, dict], occurrences: dict[str, list[dict]]) -> None:
        self.symbols = dict(symbols)
        self._occurrences = dict(occurrences)
        self._file_symbols: dict[str, list[str]] | None = None
        self._file_buckets: dict[str, set[str]] = {}
        self._owned: set[str] = set()
        self._runs: dict[str, dict[int, list[dict]]] = {}
        self._file_runs: dict[str, list[tuple[str, int]]] = {}
        self._serials = count()

    def _ensure_indexed(self) -> None:
        if self._file_symbols is not None:
            return
        self._file_symbols = {}
        for symbol_id, data in self.symbols.items():
            self._file_symbols.setdefault(data.get("file_path"), []).append(symbol_id)
        # Filtering by file drops every empty bucket, not only emptied ones.
        self._occurrences = {name: rows for name, rows in self._occurrences.items() if rows}
        for name, rows in self._occurrences.items():
            for path in {row.get("file_path") for row in rows}:
                self._file_buckets.setdefault(path, set()).add(name)

    def _new_run(self, name: str, path: str | None, rows: list[dict]) -> None:
        key = next(self._serials)
        self._runs[name][key] = rows
        self._file_runs.setdefault(path, []).append((name, key))

    def _split(self, name: str) -> None:
        rows = self._occurrences[name]
        self._owned.discard(name)
        self._runs[name] = {}
        start = 0
        for path, group in groupby([row.get("file_path") for row in rows]):
            end = start + len(list(group))
            self._new_run(name, path, rows[start:end])
            start = end

    def remove_file(self, path: str) -> None:
        self._ensure_indexed()
        for symbol_id in self._file_symbols.pop(path, ()):
            data = self.symbols.get(symbol_id)
            if data is not None and data.get("file_path") == path:
                del self.symbols[symbol_id]
        for name in self._file_buckets.pop(path, ()):
            if name in self._owned:
                self._split(name)
            elif name in self._occurrences and name not in self._runs:
                kept = [row for row in self._occurrences[name] if row.get("file_path") != path]
                if kept:
                    self._occurrences[name] = kept
                    self._owned.add(name)
                else:
                    del self._occurrences[name]
        for name, key in self._file_runs.pop(path, ()):
            bucket = self._runs[name]
            del bucket[key]
            if not bucket:
                del self._runs[name]
                del self._occurrences[name]

    def add_symbol(self, symbol_id: str, data: dict) -> None:
        self._ensure_indexed()
        self.symbols[symbol_id] = data
        self._file_symbols.setdefault(data["file_path"], []).append(symbol_id)

    def add_occurrence(self, row: dict) -> None:
        self._ensure_indexed()
        name, path = row["name"], row["file_path"]
        bucket = self._runs.get(name)
        if bucket is not None:
            last = bucket[next(reversed(bucket))]
            if last[0].get("file_path") == path:
                last.append(row)
            else:
                self._new_run(name, path, [row])
            return
        rows = self._occurrences.get(name)
        if rows is None:
            rows = self._occurrences[name] = []
        elif name not in self._owned:
            rows = self._occurrences[name] = list(rows)
        self._owned.add(name)
        rows.append(row)
        self._file_buckets.setdefault(path, set()).add(name)

    def occurrences(self) -> dict[str, list[dict]]:
        """Return the merged buckets; call once, after the last change."""
        for name, bucket in self._runs.items():
            self._occurrences[name] = [row for rows in bucket.values() for row in rows]
        return self._occurrences


def index_folder(
    repo_path: str,
    storage_dir: Path | str | None = None,
    max_files: int = 500,
    incremental: bool = True,
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Index all symbols in a local folder.

    Args:
        repo_path: Absolute path to the repository/folder root
        storage_dir: Optional override for the symbol index storage directory
        max_files: Maximum changed/new files to parse (default: 500).
                   Unchanged files are checked but do not consume this budget.
        incremental: If True (default), skip files whose SHA-256 hash matches
                     the stored index — only re-parse changed/new files.
                     Set to False to force a full re-index.

    Returns:
        Dict with files_indexed, symbols_indexed, occurrences_indexed,
        files_skipped, and repo_path.
    """
    result, _ = _index_folder_with_snapshot(
        repo_path, storage_dir, max_files, incremental, deadline=deadline, cancel_event=cancel_event
    )
    return result


def _index_folder_with_snapshot(
    repo_path: str,
    storage_dir: Path | str | None = None,
    max_files: int = 500,
    incremental: bool = True,
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[dict, CodeIndex | None]:
    """Return the published snapshot with its counts, without reloading JSON."""

    def check_stop():
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Symbol indexing stopped during shutdown")

    check_stop()
    # Input validation
    if not repo_path or not repo_path.strip():
        return error_response("repo_path must not be empty"), None

    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(f"Path does not exist or is not a directory: {repo_path}"), None

    store = IndexStore(storage_dir=storage_dir)
    resolved_root = str(root.resolve())

    # Serialize the whole load -> walk -> merge -> publish/check window per
    # repo (AC10). Without this lock two concurrent runs interleave
    # A-load, B-load, A-save, B-save and the second save, computed from a
    # stale snapshot, silently discards the first run's symbols. The lock
    # deliberately covers the (slow) walk as well: deletion detection and
    # the merge consume the walk's output, so the snapshot must stay
    # coherent for the entire window. Per-key — unrelated repos never
    # serialize. index_repo is NOT locked: it performs no load() and
    # writes github: keys, a namespace disjoint from local paths.
    with store.repo_lock(resolved_root):
        check_stop()
        checked_at_ns = time.time_ns()
        # Load existing index for incremental comparison
        existing_index = store.load(resolved_root) if incremental else None
        existing_files = existing_index.files if existing_index else {}

        # Safe refresh migration: old indexes without occurrence data must be
        # fully re-parsed before callers can trust occurrence results. This is
        # bounded by max_files and preserves the existing file hash map.
        needs_occurrence_refresh = (
            existing_index is None
            or _version_tuple(existing_index.version) < _MIN_VERSION_FOR_OCCURRENCES
        )

        # Walk source files
        from lgrep.discovery import FileDiscovery

        discovery = FileDiscovery(root)
        files_dict: dict[str, str] = dict(existing_files)  # start from existing
        merge = (
            _IndexMerge(existing_index.symbols, existing_index.occurrences)
            if existing_index
            else _IndexMerge({}, {})
        )

        # Track every code file we observed on disk in this walk so we can detect
        # deletions after the loop. Distinct from `files_dict` (the post-state)
        # because files_dict still carries entries from prior indexes that may no
        # longer exist on disk.
        walked_files: dict[str, str] = {}
        walk_truncated = False
        truncation_reason = None

        files_processed = 0
        files_skipped = 0
        for file_path in discovery.find_files():
            check_stop()
            if deadline is not None and time.monotonic() >= deadline:
                walk_truncated = True
                truncation_reason = "deadline"
                break
            if get_language_spec(file_path.suffix.lower()) is None:
                continue

            try:
                content = file_path.read_bytes()
            except OSError:
                continue

            rel_path = str(file_path.relative_to(root))
            file_hash = hashlib.sha256(content).hexdigest()
            walked_files[rel_path] = file_hash

            # Incremental skip: file unchanged and occurrence data is current
            if (
                incremental
                and existing_files.get(rel_path) == file_hash
                and not needs_occurrence_refresh
            ):
                files_skipped += 1
                continue

            if files_processed >= max_files:
                walk_truncated = True
                truncation_reason = "max_files"
                break

            files_dict[rel_path] = file_hash

            # Remove old symbols and occurrences for this file before re-parsing
            if incremental:
                merge.remove_file(rel_path)

            symbols, occurrences = _extractor.extract_full(file_path, repo_root=root)
            for sym in symbols:
                symbol_id = sym.id
                if symbol_id in merge.symbols:
                    symbol_id = f"{sym.id}@{sym.start_byte}"

                merge.add_symbol(
                    symbol_id,
                    {
                        "id": symbol_id,
                        "name": sym.name,
                        "kind": sym.kind,
                        "file_path": sym.file_path,
                        "start_byte": sym.start_byte,
                        "end_byte": sym.end_byte,
                        "docstring": sym.docstring,
                        "decorators": sym.decorators,
                        "parent": sym.parent,
                    },
                )

            is_test_file = _is_test_file_heuristic(rel_path)
            for occ in occurrences:
                merge.add_occurrence(_occurrence_to_dict(occ, is_test_file))

            files_processed += 1

        # Detect files that disappeared from disk since the last index and prune
        # them. On a complete walk the set difference against the previous index
        # is exact. A truncated walk must not use that difference — unscanned
        # files would falsely appear "deleted" — so it prunes only indexed
        # entries confirmed missing on disk, which leaves out-of-window entries
        # intact and lets the freshness gate's deletion branch settle.
        files_deleted = 0
        if incremental:
            if walk_truncated:
                deleted_set = {
                    path
                    for path in existing_files.keys() - walked_files.keys()
                    if not (root / path).exists()
                }
            else:
                deleted_set = existing_files.keys() - walked_files.keys()
            if deleted_set:
                for path in deleted_set:
                    files_dict.pop(path, None)
                    merge.remove_file(path)
                files_deleted = len(deleted_set)

        symbols_dict = merge.symbols
        occurrences_dict = merge.occurrences()
        index = CodeIndex(
            repo_path=resolved_root,
            files=files_dict,
            symbols=symbols_dict,
            occurrences=occurrences_dict,
            version=_INDEX_VERSION,
            walk_truncated=walk_truncated,
        )
        check_stop()
        if (
            existing_index is None
            or files_processed
            or files_deleted
            or existing_index.repo_path != resolved_root
            or existing_index.version != _INDEX_VERSION
            or existing_index._storage_version != STORAGE_VERSION
            or existing_index.walk_truncated != walk_truncated
        ):
            store.save(index)
        else:
            index = existing_index
            store.mark_checked(index, checked_at_ns)

    occurrence_count = sum(len(occs) for occs in occurrences_dict.values())
    log.info(
        "index_folder_complete",
        repo=str(root),
        files=files_processed,
        files_skipped=files_skipped,
        files_deleted=files_deleted,
        symbols=len(symbols_dict),
        occurrences=occurrence_count,
        incremental=incremental,
        refreshed_occurrences=needs_occurrence_refresh,
        walk_truncated=walk_truncated,
        truncation_reason=truncation_reason,
    )

    return {
        "repo_path": resolved_root,
        "files_indexed": files_processed,
        "files_skipped": files_skipped,
        "files_deleted": files_deleted,
        "symbols_indexed": len(symbols_dict),
        "occurrences_indexed": occurrence_count,
        "walk_truncated": walk_truncated,
        "truncation_reason": truncation_reason,
    }, index
