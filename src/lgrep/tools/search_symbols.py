"""lgrep_search_symbols tool implementation.

Searches for symbols by name (substring/prefix match) within an indexed repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.tools._index_freshness import refresh_stale_index
from lgrep.tools._meta import error_response

if TYPE_CHECKING:
    from pathlib import Path


def search_symbols(
    query: str,
    repo_path: str,
    storage_dir: Path | str | None = None,
    limit: int = 20,
    kind: str | None = None,
) -> dict:
    """Search for symbols by name in an indexed repository.

    Performs case-insensitive substring matching on symbol names.

    Args:
        query: Search query (matched against symbol names)
        repo_path: Absolute path to the indexed repository
        storage_dir: Optional override for the symbol index storage directory
        limit: Maximum number of results to return (default: 20)
        kind: Optional filter by symbol kind (function, class, method, etc.)

    Returns:
        Dict with results list and total_matches.
        Returns error dict if the repo has not been indexed.
    """
    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty")
    if limit < 0:
        limit = 1

    store = IndexStore(storage_dir=storage_dir)

    repo_key = normalize_repo_key(repo_path)
    # First use of an unindexed local git checkout builds that checkout's
    # own index (seeded from an indexed sibling worktree when one exists)
    # instead of refusing.
    bootstrapped = False
    if store.load(repo_key) is None:
        from lgrep.tools._index_bootstrap import ensure_symbol_index

        bootstrapped = ensure_symbol_index(repo_path, storage_dir=storage_dir)
    # Serve no answer from an index known to be behind the working tree:
    # refresh first when the gate fires, then load the post-refresh index.
    refresh = refresh_stale_index(repo_path, storage_dir=storage_dir)
    index = store.load(repo_key)
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
        "index_refreshed": refresh is not None or bootstrapped,
    }
