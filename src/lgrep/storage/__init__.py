"""lgrep storage package.

Re-exports the semantic chunk store (formerly storage.py) alongside
the new symbol storage modules (token_tracker, index_store).

Existing imports like `from lgrep.storage import ChunkStore` continue to work.
"""

# Re-export everything from the chunk store (semantic storage)
from lgrep.storage._chunk_store import (  # noqa: F401
    CHUNKS_TABLE,
    EMBEDDING_DIM,
    ChunkStore,
    CodeChunk,
    SearchResult,
    SearchResults,
    discover_cached_projects,
    get_project_db_path,
    has_disk_cache,
    write_project_meta,
)
