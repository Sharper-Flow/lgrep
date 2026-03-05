"""lgrep_search_symbols tool implementation.

Searches for symbols by name (substring/prefix match) within an indexed repository.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

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
        Dict with results list, total_matches, and _meta envelope.
        Returns error dict if the repo has not been indexed.
    """
    t0 = time.monotonic()

    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty", _meta=make_meta(t0))
    if limit < 0:
        limit = 1

    store = IndexStore(storage_dir=storage_dir)

    repo_key = normalize_repo_key(repo_path)
    index = store.load(repo_key)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
            _meta=make_meta(t0),
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

    tokens_saved = estimate_savings(len(results))
    return {
        "results": results,
        "total_matches": len(results),
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
