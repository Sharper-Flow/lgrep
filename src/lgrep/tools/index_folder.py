"""lgrep_index_symbols_folder tool implementation.

Indexes all symbols and candidate Python/Go occurrences in a local folder and
persists to IndexStore.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import structlog

from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec
from lgrep.storage.index_store import CodeIndex, IndexStore, _version_tuple
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

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


def _occurrence_to_dict(occ, rel_path: str) -> dict:
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
        "is_test_file": _is_test_file_heuristic(rel_path),
    }


def index_folder(
    repo_path: str,
    storage_dir: Path | str | None = None,
    max_files: int = 500,
    incremental: bool = True,
) -> dict:
    """Index all symbols in a local folder.

    Args:
        repo_path: Absolute path to the repository/folder root
        storage_dir: Optional override for the symbol index storage directory
        max_files: Maximum number of files to index (default: 500)
        incremental: If True (default), skip files whose SHA-256 hash matches
                     the stored index — only re-parse changed/new files.
                     Set to False to force a full re-index.

    Returns:
        Dict with files_indexed, symbols_indexed, occurrences_indexed,
        files_skipped, repo_path, and _meta envelope.
    """
    t0 = time.monotonic()

    # Input validation
    if not repo_path or not repo_path.strip():
        return error_response("repo_path must not be empty", _meta=make_meta(t0, __name__))

    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0, __name__),
        )

    store = IndexStore(storage_dir=storage_dir)
    resolved_root = str(root.resolve())

    # Serialize the whole load -> walk -> merge -> save window per repo
    # (AC10). Incremental indexing is a read-modify-write ACROSS the
    # IndexStore API: load() above, a second load() inside detect_changes()
    # below, then save(). Without this lock two concurrent runs interleave
    # A-load, B-load, A-save, B-save and the second save, computed from a
    # stale snapshot, silently discards the first run's symbols. The lock
    # deliberately covers the (slow) walk as well: detect_changes() and
    # the merge consume the walk's output, so the snapshot must stay
    # coherent for the entire window. Per-key — unrelated repos never
    # serialize. index_repo is NOT locked: it performs no load() and
    # writes github: keys, a namespace disjoint from local paths.
    with store.repo_lock(resolved_root):
        # Load existing index for incremental comparison
        existing_index = store.load(resolved_root) if incremental else None
        existing_files = existing_index.files if existing_index else {}
        existing_symbols = dict(existing_index.symbols) if existing_index else {}
        existing_occurrences = dict(existing_index.occurrences) if existing_index else {}

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
        symbols_dict: dict[str, dict] = dict(existing_symbols)
        occurrences_dict: dict[str, list[dict]] = {
            name: list(occs) for name, occs in (existing_occurrences or {}).items()
        }

        # Track every code file we observed on disk in this walk so we can detect
        # deletions after the loop. Distinct from `files_dict` (the post-state)
        # because files_dict still carries entries from prior indexes that may no
        # longer exist on disk.
        walked_files: dict[str, str] = {}
        walk_truncated = False

        files_processed = 0
        files_skipped = 0
        for file_path in discovery.find_files():
            if files_processed + files_skipped >= max_files:
                walk_truncated = True
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

            files_dict[rel_path] = file_hash

            # Remove old symbols and occurrences for this file before re-parsing
            if incremental:
                symbols_dict = {
                    sid: sdata
                    for sid, sdata in symbols_dict.items()
                    if sdata.get("file_path") != rel_path
                }
                occurrences_dict = {
                    name: [occ for occ in occs if occ.get("file_path") != rel_path]
                    for name, occs in occurrences_dict.items()
                }
                # Drop empty occurrence buckets
                occurrences_dict = {name: occs for name, occs in occurrences_dict.items() if occs}

            symbols, occurrences = _extractor.extract_full(file_path, repo_root=root)
            for sym in symbols:
                symbol_id = sym.id
                if symbol_id in symbols_dict:
                    symbol_id = f"{sym.id}@{sym.start_byte}"

                symbols_dict[symbol_id] = {
                    "id": symbol_id,
                    "name": sym.name,
                    "kind": sym.kind,
                    "file_path": sym.file_path,
                    "start_byte": sym.start_byte,
                    "end_byte": sym.end_byte,
                    "docstring": sym.docstring,
                    "decorators": sym.decorators,
                    "parent": sym.parent,
                }

            for occ in occurrences:
                occs = occurrences_dict.setdefault(occ.name, [])
                occs.append(_occurrence_to_dict(occ, rel_path))

            files_processed += 1

        # Detect files that disappeared from disk since the last index and prune
        # them. Only safe to do when we walked the full tree — if max_files
        # truncated the walk, unscanned files would falsely appear "deleted".
        files_deleted = 0
        if incremental and not walk_truncated:
            changes = store.detect_changes(resolved_root, walked_files)
            deleted_set = set(changes.get("deleted", []))
            if deleted_set:
                for path in deleted_set:
                    files_dict.pop(path, None)
                symbols_dict = {
                    sid: sdata
                    for sid, sdata in symbols_dict.items()
                    if sdata.get("file_path") not in deleted_set
                }
                occurrences_dict = {
                    name: [occ for occ in occs if occ.get("file_path") not in deleted_set]
                    for name, occs in occurrences_dict.items()
                }
                occurrences_dict = {name: occs for name, occs in occurrences_dict.items() if occs}
                files_deleted = len(deleted_set)

        index = CodeIndex(
            repo_path=resolved_root,
            files=files_dict,
            symbols=symbols_dict,
            occurrences=occurrences_dict,
            version=_INDEX_VERSION,
        )
        store.save(index)

    occurrence_count = sum(len(occs) for occs in occurrences_dict.values())
    tokens_saved = estimate_savings(len(symbols_dict) + occurrence_count)
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
    )

    return {
        "repo_path": resolved_root,
        "files_indexed": files_processed,
        "files_skipped": files_skipped,
        "files_deleted": files_deleted,
        "symbols_indexed": len(symbols_dict),
        "occurrences_indexed": occurrence_count,
        "_meta": make_meta(t0, __name__, tokens_saved=tokens_saved),
    }
