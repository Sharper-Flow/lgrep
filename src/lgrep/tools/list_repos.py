"""lgrep_list_repos tool implementation.

Lists all repositories that have been indexed in the symbol store.
"""

from __future__ import annotations

import time
from pathlib import Path

from lgrep.storage.index_store import IndexStore
from lgrep.tools._meta import make_meta


def list_repos(storage_dir: Path | str | None = None) -> dict:
    """List all indexed repositories.

    Args:
        storage_dir: Optional override for the symbol index storage directory

    Returns:
        Dict with repos list and _meta envelope
    """
    t0 = time.monotonic()
    store = IndexStore(storage_dir=storage_dir)
    repos = store.list_repos()

    return {
        "repos": repos,
        "count": len(repos),
        "_meta": make_meta(t0),
    }
