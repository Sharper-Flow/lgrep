"""lgrep_invalidate_cache tool implementation.

Removes the symbol index for a repository, forcing a full re-index on next use.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.tools._meta import make_meta

if TYPE_CHECKING:
    from pathlib import Path


def invalidate_cache(
    repo_path: str,
    storage_dir: Path | str | None = None,
) -> dict:
    """Remove the symbol index for a repository.

    After invalidation, the next call to lgrep_index_symbols_folder will
    perform a full re-index.

    Args:
        repo_path: Absolute path to the repository root
        storage_dir: Optional override for the symbol index storage directory

    Returns:
        Dict with status ("deleted" or "not_found") and _meta envelope
    """
    t0 = time.monotonic()
    store = IndexStore(storage_dir=storage_dir)

    repo_key = normalize_repo_key(repo_path)
    existing = store.load(repo_key)

    if existing is None:
        return {
            "status": "not_found",
            "repo_path": repo_key,
            "_meta": make_meta(t0),
        }

    store.delete_index(repo_key)
    return {
        "status": "deleted",
        "repo_path": repo_key,
        "_meta": make_meta(t0),
    }
