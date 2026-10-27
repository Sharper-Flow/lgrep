"""Freshness gate for the persisted symbol index.

search_answers must not come from an index that is known to be behind the
working tree. The semantic engine already enforces this with a three-stage
gate (mtime, hash, re-index) before it serves a query; this module gives the
symbol engine the same shape before ``search_symbols`` answers.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from lgrep.parser.languages import get_language_spec
from lgrep.storage.index_store import CodeIndex, IndexStore, normalize_repo_key

log = structlog.get_logger()


def auto_refresh_enabled() -> bool:
    """LGREP_AUTO_REFRESH=0 opts out; every other value keeps the gate on."""
    return os.environ.get("LGREP_AUTO_REFRESH", "1") != "0"


def _index_is_behind(
    root: Path,
    index_files: dict[str, str],
    indexed_at: float,
    walk_truncated: bool,
) -> bool:
    """Compare the walked working tree against the index in one pass.

    Fires when a source file is newer than the recorded index timestamp
    (last publication, or two seconds before the last no-op check started),
    or when the index cannot account for the walked tree (set branch).
    The set branch differs by index kind:

    - Complete index (the marker absent or False): any set difference is
      staleness — additions, or deletions the refresh will prune.
    - Truncated index (the walk stopped at max_files): the un-indexed
      remainder makes the set difference permanent, so additions alone
      never fire — otherwise every query on a repo past max_files would
      re-index and re-save an unchanged index forever. Only indexed
      entries confirmed missing on disk (deletions) count as staleness;
      the refresher prunes confirmed deletions even on truncated walks.
    """
    from lgrep.discovery import FileDiscovery

    walked: set[str] = set()
    for file_path in FileDiscovery(root).find_files():
        if get_language_spec(file_path.suffix.lower()) is None:
            continue
        try:
            rel_path = str(file_path.relative_to(root))
            mtime = file_path.stat().st_mtime
        except OSError:
            continue
        walked.add(rel_path)
        if mtime > indexed_at:
            return True
    if walked == set(index_files):
        return False
    if not walk_truncated:
        return True
    return any(not (root / rel_path).exists() for rel_path in set(index_files) - walked)


def load_fresh_index(
    repo_path: str, storage_dir: Path | str | None = None
) -> tuple[CodeIndex | None, bool]:
    """Load once and return the checked snapshot plus whether a refresh ran.

    A refresh takes its own current snapshot under the writer lock and returns
    its result directly. Oversized indexes need not be globally retained or
    repeatedly deserialized within this query. A failed refresh serves the
    original snapshot, matching the query's best-effort freshness contract.
    """
    store = IndexStore(storage_dir=storage_dir)
    repo_key = normalize_repo_key(repo_path)
    # Read the timestamp before the body. A concurrent publication must not
    # make an older caller-held snapshot appear checked through its new mtime.
    indexed_at = store.last_indexed_at(repo_key)
    index = store.load(repo_key)
    if index is None or not auto_refresh_enabled() or indexed_at is None:
        return index, False
    root = Path(repo_path)
    if repo_path.startswith("github:") or not root.is_dir():
        return index, False

    try:
        behind = _index_is_behind(
            root,
            index.files,
            indexed_at,
            walk_truncated=index.walk_truncated,
        )
    except OSError as e:
        log.warning("index_freshness_check_failed", repo=repo_key, error=str(e))
        return index, False
    if not behind:
        return index, False

    from lgrep.tools.index_folder import _index_folder_with_snapshot

    result, refreshed_index = _index_folder_with_snapshot(repo_path, storage_dir=storage_dir)
    if "error" in result:
        log.warning("index_auto_refresh_failed", repo=repo_key, error=result["error"])
        return index, False
    log.info(
        "index_auto_refreshed",
        repo=repo_key,
        files_indexed=result.get("files_indexed", 0),
        files_deleted=result.get("files_deleted", 0),
    )
    return refreshed_index, True
