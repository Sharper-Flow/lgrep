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
from lgrep.storage.index_store import IndexStore, normalize_repo_key

log = structlog.get_logger()


def auto_refresh_enabled() -> bool:
    """LGREP_AUTO_REFRESH=0 opts out; every other value keeps the gate on."""
    return os.environ.get("LGREP_AUTO_REFRESH", "1") != "0"


def _index_is_behind(root: Path, index_files: dict[str, str], indexed_at: float) -> bool:
    """Compare the walked working tree against the index in one pass.

    Fires when any source file is newer than the last index window closed
    (mtime branch) or when the walked file set differs from the indexed file
    set (set branch — the only signal for deletions, which change no mtime).
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
    return walked != set(index_files)


def refresh_stale_index(repo_path: str, storage_dir: Path | str | None = None) -> dict | None:
    """Incrementally re-index ``repo_path`` when its index is behind the tree.

    Returns the index_folder result dict when a refresh ran, and None when no
    refresh was needed, the gate is disabled, or the path is not a local
    repository backed by the working tree (remote ``github:`` keys are out of
    scope: their content is not on this disk). A failed refresh returns None
    so the caller serves the index it has rather than erroring.
    """
    if not auto_refresh_enabled():
        return None
    if repo_path.startswith("github:"):
        return None
    root = Path(repo_path)
    if not root.is_dir():
        return None

    store = IndexStore(storage_dir=storage_dir)
    repo_key = normalize_repo_key(repo_path)
    index = store.load(repo_key)
    if index is None:
        # Unindexed repos are search_symbols' own "not indexed" error, not
        # a staleness problem.
        return None
    indexed_at = store.last_indexed_at(repo_key)
    if indexed_at is None:
        return None

    try:
        behind = _index_is_behind(root, index.files, indexed_at)
    except OSError as e:
        log.warning("index_freshness_check_failed", repo=repo_key, error=str(e))
        return None
    if not behind:
        return None

    from lgrep.tools.index_folder import index_folder

    result = index_folder(repo_path, storage_dir=storage_dir)
    if "error" in result:
        log.warning("index_auto_refresh_failed", repo=repo_key, error=result["error"])
        return None
    log.info(
        "index_auto_refreshed",
        repo=repo_key,
        files_indexed=result.get("files_indexed", 0),
        files_deleted=result.get("files_deleted", 0),
    )
    return result
