"""LanceDB storage for lgrep code chunks.

Stores code chunks with embeddings in a local LanceDB database.
Supports hybrid search (vector + FTS) with RRF reranking.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import lancedb
import structlog
from lancedb.pydantic import LanceModel, Vector
from lancedb.rerankers import RRFReranker
from pydantic import Field

if TYPE_CHECKING:
    from lancedb import DBConnection
    from lancedb.table import Table

log = structlog.get_logger()

# Default cache directory
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lgrep"

# Voyage Code 3 embedding dimensions
EMBEDDING_DIM = 1024

# Table name
CHUNKS_TABLE = "chunks"


def _escape_sql_string(value: str) -> str:
    """Escape a string for use in a LanceDB SQL predicate.

    LanceDB's delete()/where() only accept raw SQL predicate strings, not
    parameterized queries. We escape single quotes using SQL standard doubling
    to prevent injection via crafted values (e.g. file paths containing quotes).
    """
    return value.replace("'", "''")


class CodeChunk(LanceModel):
    """LanceDB model for a code chunk.

    Stores code content with embedding vector for semantic search.
    """

    id: str = Field(description="Unique identifier (uuid)")
    file_path: str = Field(description="Relative path from project root")
    chunk_index: int = Field(description="Position of chunk in file")
    start_line: int = Field(description="Starting line number (1-indexed)")
    end_line: int = Field(description="Ending line number (inclusive)")
    content: str = Field(description="Chunk text content")
    vector: Vector(EMBEDDING_DIM) = Field(description="Voyage Code 3 embedding")  # type: ignore[valid-type]
    file_hash: str = Field(description="Hash of source file for invalidation")
    indexed_at: float = Field(description="Unix timestamp of indexing")


@dataclass
class SearchResult:
    """A single search result."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float
    match_type: str = "hybrid"


@dataclass
class SearchResults:
    """Results from a search query."""

    results: list[SearchResult] = field(default_factory=list)
    query_time_ms: float = 0.0
    total_chunks: int = 0


def get_project_db_path(project_path: str | Path) -> Path:
    """Get the database path for a project.

    Creates a unique path based on the project's absolute path hash.

    Args:
        project_path: Path to the project directory

    Returns:
        Path to the project's LanceDB directory
    """
    project_path = Path(project_path).resolve()
    path_hash = hashlib.sha256(str(project_path).encode()).hexdigest()[:12]

    cache_dir = Path(os.environ.get("LGREP_CACHE_DIR", DEFAULT_CACHE_DIR))
    return cache_dir / path_hash


def has_disk_cache(project_path: str | Path) -> bool:
    """Check whether a project has an existing LanceDB index on disk.

    Looks for the ``chunks.lance`` directory inside the project's cache
    directory.  This is a pure filesystem check — it does not open the
    database or require an API key.

    Args:
        project_path: Path to the project directory.

    Returns:
        True if a chunks table exists on disk for this project.
    """
    db_path = get_project_db_path(project_path)
    return (db_path / (CHUNKS_TABLE + ".lance")).is_dir()


class ChunkStore:
    """LanceDB-backed storage for code chunks.

    Provides vector and hybrid search over indexed code chunks.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the chunk store.

        Args:
            db_path: Path to the LanceDB database directory
        """
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        try:
            self.db: DBConnection = lancedb.connect(str(self.db_path))
        except Exception as e:
            log.warning(
                "chunk_store_connection_failed",
                db_path=str(self.db_path),
                error=str(e),
                action="clearing and reconnecting",
            )
            # Clear corrupted data and retry
            import shutil

            for item in self.db_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            self.db = lancedb.connect(str(self.db_path))

        self._table: Table | None = None
        self._fts_indexed = False

        log.info("chunk_store_connected", db_path=str(self.db_path))

    @property
    def table(self) -> Table:
        """Get or create the chunks table."""
        if self._table is None:
            try:
                table_names = self.db.list_tables()
                if CHUNKS_TABLE in table_names:
                    self._table = self.db.open_table(CHUNKS_TABLE)
                    log.debug("chunk_table_opened", rows=self._table.count_rows())
                else:
                    # Create empty table with schema
                    self._table = self.db.create_table(
                        CHUNKS_TABLE,
                        schema=CodeChunk.to_arrow_schema(),
                    )
                    log.info("chunk_table_created")
            except Exception as e:
                log.warning(
                    "chunk_table_open_failed",
                    error=str(e),
                    action="dropping and recreating table",
                )
                # Drop corrupted table and recreate
                try:
                    self.db.drop_table(CHUNKS_TABLE, ignore_missing=True)
                except Exception as drop_err:
                    log.debug("drop_table_also_failed", error=str(drop_err))
                self._table = self.db.create_table(
                    CHUNKS_TABLE,
                    schema=CodeChunk.to_arrow_schema(),
                )
                log.info("chunk_table_recreated_after_corruption")
        return self._table

    def add_chunks(self, chunks: list[CodeChunk]) -> int:
        """Add chunks to the store.

        Args:
            chunks: List of CodeChunk objects to add

        Returns:
            Number of chunks added
        """
        if not chunks:
            return 0

        # Convert to dicts for LanceDB
        data = [chunk.model_dump() for chunk in chunks]
        self.table.add(data)

        log.info("chunks_added", count=len(chunks))
        return len(chunks)

    def upsert_chunks(self, chunks: list[CodeChunk]) -> int:
        """Upsert chunks (update existing, insert new).

        Args:
            chunks: List of CodeChunk objects to upsert

        Returns:
            Number of chunks upserted
        """
        if not chunks:
            return 0

        data = [chunk.model_dump() for chunk in chunks]

        # Use merge_insert for upsert
        self.table.merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(data)

        log.info("chunks_upserted", count=len(chunks))
        return len(chunks)

    def delete_by_file(self, file_path: str) -> int:
        """Delete all chunks for a file.

        Args:
            file_path: Relative path of the file

        Returns:
            Number of chunks deleted (approximate)
        """
        before_count = self.table.count_rows()
        safe_path = _escape_sql_string(file_path)
        self.table.delete(f"file_path = '{safe_path}'")
        after_count = self.table.count_rows()

        deleted = before_count - after_count
        log.info("chunks_deleted", file_path=file_path, count=deleted)
        return deleted

    def ensure_fts_index(self) -> None:
        """Ensure the FTS index exists on the content column."""
        if not self._fts_indexed:
            try:
                self.table.create_fts_index("content", replace=True)
                self._fts_indexed = True
                log.info("fts_index_created")
            except Exception as e:
                log.warning("fts_index_failed", error=str(e))

    def search_hybrid(
        self,
        query_vector: list[float],
        query_text: str,
        limit: int = 10,
    ) -> SearchResults:
        """Perform hybrid search (vector + FTS with RRF reranking).

        Args:
            query_vector: Embedding vector for the query
            query_text: Original query text for FTS
            limit: Maximum results to return

        Returns:
            SearchResults with ranked results
        """
        start = time.perf_counter()

        # Ensure FTS index exists
        self.ensure_fts_index()

        # Create vector index if needed (for large tables)
        row_count = self.table.count_rows()
        if row_count > 1000:
            try:
                self.table.create_index("vector", replace=True)
            except Exception as idx_err:
                log.debug("vector_index_create_skipped", error=str(idx_err))

        # Hybrid search with RRF reranking
        reranker = RRFReranker()
        raw_results = (
            self.table.search(query_type="hybrid")
            .vector(query_vector)
            .text(query_text)
            .rerank(reranker)
            .limit(limit)
            .to_list()
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        results = [
            SearchResult(
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                score=r.get("_relevance_score", r.get("_distance", 0.0)),
                match_type="hybrid",
            )
            for r in raw_results
        ]

        return SearchResults(
            results=results,
            query_time_ms=elapsed_ms,
            total_chunks=row_count,
        )

    def search_vector(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> SearchResults:
        """Perform vector-only search.

        Args:
            query_vector: Embedding vector for the query
            limit: Maximum results to return

        Returns:
            SearchResults with ranked results
        """
        start = time.perf_counter()

        raw_results = self.table.search(query_vector).limit(limit).to_list()

        elapsed_ms = (time.perf_counter() - start) * 1000

        results = [
            SearchResult(
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                score=r.get("_distance", 0.0),
                match_type="vector",
            )
            for r in raw_results
        ]

        return SearchResults(
            results=results,
            query_time_ms=elapsed_ms,
            total_chunks=self.table.count_rows(),
        )

    def count_chunks(self) -> int:
        """Get total chunk count."""
        return self.table.count_rows()

    def get_file_hash(self, file_path: str) -> str | None:
        """Get the stored hash for a file, if it exists."""
        try:
            # Query just one chunk for this file to get its stored hash
            safe_path = _escape_sql_string(file_path)
            results = (
                self.table.search()
                .where(f"file_path = '{safe_path}'")
                .limit(1)
                .select(["file_hash"])
                .to_list()
            )
            if results:
                return results[0]["file_hash"]
            return None
        except Exception as e:
            log.debug("get_file_hash_failed", file_path=file_path, error=str(e))
            return None

    def get_indexed_files(self) -> set[str]:
        """Get set of indexed file paths.

        Uses column projection to avoid loading vectors into memory.
        For 75k chunks, this loads only the file_path column instead of
        the entire table (including 1024-dim vectors).
        """
        try:
            arrow_table = (
                self.table.search().select(["file_path"]).limit(self.table.count_rows()).to_arrow()
            )
            file_paths = arrow_table.column("file_path").to_pylist()
            return set(file_paths)
        except Exception as e:
            log.debug("get_indexed_files_failed", error=str(e))
            return set()

    def clear(self) -> None:
        """Clear all chunks from the store."""
        self.db.drop_table(CHUNKS_TABLE, ignore_missing=True)
        self._table = None
        self._fts_indexed = False
        log.info("chunk_store_cleared")
