"""lgrep_search_symbols tool implementation.

Searches for symbols by name (substring/prefix match) within an indexed repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore
from lgrep.tools._index_freshness import load_fresh_index
from lgrep.tools._meta import error_response

if TYPE_CHECKING:
    from pathlib import Path

    from lgrep.storage.index_store import CodeIndex


def load_symbol_index(
    repo_path: str, storage_dir: Path | str | None = None
) -> tuple[CodeIndex | None, bool]:
    """Acquire a checked snapshot, bootstrapping an unindexed local checkout."""
    index, refreshed = load_fresh_index(repo_path, storage_dir=storage_dir)
    bootstrapped = False
    if index is None:
        from lgrep.tools._index_bootstrap import ensure_symbol_index

        bootstrapped = ensure_symbol_index(repo_path, storage_dir=storage_dir)
        index = IndexStore(storage_dir=storage_dir).load(repo_path)
    return index, refreshed or bootstrapped


def search_symbols(
    query: str,
    repo_path: str,
    storage_dir: Path | str | None = None,
    limit: int = 20,
    kind: str | None = None,
    *,
    index_snapshot: tuple[CodeIndex | None, bool] | None = None,
) -> dict:
    """Search for symbols by name in an indexed repository.

    Performs case-insensitive substring matching on symbol names.

    Args:
        query: Search query (matched against symbol names)
        repo_path: Absolute path to the indexed repository
        storage_dir: Optional override for the symbol index storage directory
        limit: Maximum number of results to return (default: 20)
        kind: Optional filter by symbol kind (function, class, method, etc.)
        index_snapshot: Checked snapshot supplied by the daemon's index owner.
                        Omit to acquire it synchronously, as CLI callers do.

    Returns:
        Dict with results list and total_matches.
        Returns error dict if the repo has not been indexed.
    """
    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty")
    if limit < 0:
        limit = 1

    index, refreshed = (
        load_symbol_index(repo_path, storage_dir) if index_snapshot is None else index_snapshot
    )
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
        )

    query_lower = query.lower()
    results = []

    for _sym_id, sym_data in index.symbols.items():
        name = sym_data.get("name", "")
        if query_lower not in name.lower():
            continue
        if kind and sym_data.get("kind") != kind:
            continue
        results.append(sym_data)
        if len(results) >= limit:
            break

    return {
        "results": results,
        "total_matches": len(results),
        "index_refreshed": refreshed,
    }
