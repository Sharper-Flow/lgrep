"""lgrep_list_repos tool implementation.

Lists all repositories that have been indexed in the symbol store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore

if TYPE_CHECKING:
    from pathlib import Path


def list_repos(storage_dir: Path | str | None = None) -> dict:
    """List all indexed repositories.

    Args:
        storage_dir: Optional override for the symbol index storage directory

    Returns:
        Dict with repos list
    """
    store = IndexStore(storage_dir=storage_dir)
    repos = store.list_repos()

    return {
        "repos": repos,
        "count": len(repos),
    }
