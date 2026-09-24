"""lgrep storage package.

Re-exports the semantic chunk store (formerly storage.py) alongside
the symbol index store.

Existing imports like `from lgrep.storage import ChunkStore` continue to work.
"""

# Re-export everything from the chunk store (semantic storage)
from lgrep.storage._chunk_store import (  # noqa: F401
    BASE_CHECKOUT,
    CHUNKS_TABLE,
    DEFAULT_CACHE_DIR,
    EMBEDDING_DIM,
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
    prune_overlays,
    read_project_meta,
    write_project_meta,
)
