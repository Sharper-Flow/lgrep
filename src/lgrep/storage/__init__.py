"""lgrep storage package.

Re-exports the semantic chunk store alongside the symbol index store, so
`from lgrep.storage import ChunkStore` works.

The chunk-store names resolve lazily (PEP 562): importing the package, or the
symbol index store inside it, must not import lancedb and pyarrow. The symbol
worker process imports only the symbol index store and would otherwise hold
those libraries in memory and pay their import time on every start.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lgrep.storage._chunk_store import (
        BASE_CHECKOUT,
        CHUNKS_TABLE,
        DEFAULT_CACHE_DIR,
        EMBEDDING_DIM,
        MAINTENANCE_VERSION_RETENTION,
        ChunkStore,
        CodeChunk,
        OverlayStore,
        SearchResult,
        SearchResults,
        canonical_repo_key,
        checkout_scope,
        discover_cached_projects,
        get_project_db_path,
        has_disk_cache,
        open_checkout_store,
        optimize_table,
        prune_overlays,
        read_project_meta,
        write_project_meta,
    )

__all__ = [
    "BASE_CHECKOUT",
    "CHUNKS_TABLE",
    "DEFAULT_CACHE_DIR",
    "EMBEDDING_DIM",
    "MAINTENANCE_VERSION_RETENTION",
    "ChunkStore",
    "CodeChunk",
    "OverlayStore",
    "SearchResult",
    "SearchResults",
    "canonical_repo_key",
    "checkout_scope",
    "discover_cached_projects",
    "get_project_db_path",
    "has_disk_cache",
    "open_checkout_store",
    "optimize_table",
    "prune_overlays",
    "read_project_meta",
    "write_project_meta",
]

_CHUNK_STORE_NAMES = frozenset(__all__)


def __getattr__(name: str) -> object:
    if name not in _CHUNK_STORE_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from lgrep.storage import _chunk_store

    value = getattr(_chunk_store, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _CHUNK_STORE_NAMES)
