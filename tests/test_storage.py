"""Tests for LanceDB storage."""

import tempfile
import time
import uuid
from pathlib import Path

import pytest

from lgrep.storage import (
    EMBEDDING_DIM,
    ChunkStore,
    CodeChunk,
    SearchResult,
    SearchResults,
    get_project_db_path,
)


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test_db"


@pytest.fixture
def chunk_store(temp_db_path):
    """Create a ChunkStore with temporary storage."""
    return ChunkStore(temp_db_path)


def make_chunk(
    file_path: str = "test.py",
    chunk_index: int = 0,
    content: str = "def test(): pass",
) -> CodeChunk:
    """Create a test chunk with random embedding."""
    import random

    return CodeChunk(
        id=str(uuid.uuid4()),
        file_path=file_path,
        chunk_index=chunk_index,
        start_line=1 + chunk_index * 10,
        end_line=10 + chunk_index * 10,
        content=content,
        vector=[random.random() for _ in range(EMBEDDING_DIM)],
        file_hash="abc123",
        indexed_at=time.time(),
    )


class TestGetProjectDbPath:
    """Tests for get_project_db_path function."""

    def test_returns_path_in_cache_dir(self):
        """Should return path under cache directory."""
        result = get_project_db_path("/home/user/myproject")
        assert ".cache/lgrep" in str(result) or "lgrep" in str(result)

    def test_different_projects_get_different_paths(self):
        """Should return different paths for different projects."""
        path1 = get_project_db_path("/project/a")
        path2 = get_project_db_path("/project/b")
        assert path1 != path2

    def test_same_project_gets_same_path(self):
        """Should return same path for same project."""
        path1 = get_project_db_path("/project/a")
        path2 = get_project_db_path("/project/a")
        assert path1 == path2


class TestCodeChunk:
    """Tests for CodeChunk model."""

    def test_create_chunk(self):
        """Should create chunk with all fields."""
        chunk = make_chunk()
        assert chunk.id
        assert chunk.file_path == "test.py"
        assert chunk.chunk_index == 0
        assert len(chunk.vector) == EMBEDDING_DIM

    def test_model_dump(self):
        """Should serialize to dict."""
        chunk = make_chunk()
        data = chunk.model_dump()
        assert "id" in data
        assert "vector" in data
        assert len(data["vector"]) == EMBEDDING_DIM


class TestChunkStore:
    """Tests for ChunkStore class."""

    def test_init_creates_directory(self, temp_db_path):
        """Should create database directory."""
        store = ChunkStore(temp_db_path)
        assert temp_db_path.exists()

    def test_add_chunks_empty(self, chunk_store):
        """Should handle empty chunk list."""
        result = chunk_store.add_chunks([])
        assert result == 0

    def test_add_chunks(self, chunk_store):
        """Should add chunks to store."""
        chunks = [make_chunk(chunk_index=i) for i in range(3)]
        result = chunk_store.add_chunks(chunks)

        assert result == 3
        assert chunk_store.count_chunks() == 3

    def test_delete_by_file(self, chunk_store):
        """Should delete chunks for a specific file."""
        chunks = [
            make_chunk(file_path="keep.py", chunk_index=0),
            make_chunk(file_path="delete.py", chunk_index=0),
            make_chunk(file_path="delete.py", chunk_index=1),
        ]
        chunk_store.add_chunks(chunks)

        deleted = chunk_store.delete_by_file("delete.py")

        assert deleted == 2
        assert chunk_store.count_chunks() == 1

    def test_search_vector(self, chunk_store):
        """Should perform vector search."""
        import random

        # Add some chunks
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(5)]
        chunk_store.add_chunks(chunks)

        # Search with random query vector
        query_vector = [random.random() for _ in range(EMBEDDING_DIM)]
        results = chunk_store.search_vector(query_vector, limit=3)

        assert len(results.results) == 3
        assert results.total_chunks == 5
        assert results.query_time_ms > 0

    def test_get_indexed_files(self, chunk_store):
        """Should return set of indexed file paths."""
        chunks = [
            make_chunk(file_path="a.py"),
            make_chunk(file_path="b.py"),
            make_chunk(file_path="a.py", chunk_index=1),
        ]
        chunk_store.add_chunks(chunks)

        files = chunk_store.get_indexed_files()
        assert files == {"a.py", "b.py"}

    def test_delete_by_file_with_quotes(self, chunk_store):
        """Should handle file paths containing single quotes without SQL injection."""
        chunks = [
            make_chunk(file_path="normal.py", chunk_index=0),
            make_chunk(file_path="it's a file.py", chunk_index=0),
        ]
        chunk_store.add_chunks(chunks)
        assert chunk_store.count_chunks() == 2

        # This should not break or cause SQL injection
        deleted = chunk_store.delete_by_file("it's a file.py")
        assert deleted == 1
        assert chunk_store.count_chunks() == 1

    def test_delete_by_file_with_sql_injection_attempt(self, chunk_store):
        """Should safely handle malicious file paths."""
        chunks = [
            make_chunk(file_path="safe.py", chunk_index=0),
            make_chunk(file_path="evil.py", chunk_index=0),
        ]
        chunk_store.add_chunks(chunks)
        assert chunk_store.count_chunks() == 2

        # Attempt SQL injection via file path - should not delete all rows
        chunk_store.delete_by_file("' OR '1'='1")
        # safe.py and evil.py should still exist (injection attempt matched nothing real)
        assert chunk_store.count_chunks() == 2

    def test_clear(self, chunk_store):
        """Should clear all chunks."""
        chunks = [make_chunk(chunk_index=i) for i in range(3)]
        chunk_store.add_chunks(chunks)
        assert chunk_store.count_chunks() == 3

        chunk_store.clear()
        assert chunk_store.count_chunks() == 0


class TestChunkStoreCorruptionRecovery:
    """Tests for graceful corruption recovery."""

    def test_corrupted_db_recovers_gracefully(self, temp_db_path):
        """Should recover from corrupted database by clearing and reconnecting."""
        # Create a valid store and add data
        store = ChunkStore(temp_db_path)
        chunks = [make_chunk(chunk_index=i) for i in range(3)]
        store.add_chunks(chunks)
        assert store.count_chunks() == 3

        # Simulate corruption by writing garbage to data files inside .lance dirs
        corrupted = False
        for f in temp_db_path.rglob("*"):
            if f.is_file() and f.suffix != "":
                f.write_bytes(b"CORRUPTED DATA")
                corrupted = True
                break
        assert corrupted, "Should have found a file to corrupt"

        # Re-opening should not crash - should recover
        store2 = ChunkStore(temp_db_path)
        # The store should be functional (either recovered or cleared)
        # count_chunks should not raise
        count = store2.count_chunks()
        assert isinstance(count, int)

    def test_init_with_nonexistent_path_creates_dir(self):
        """Should create directory if it doesn't exist."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "deep" / "nested" / "db"
            store = ChunkStore(db_path)
            assert db_path.exists()


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_create_result(self):
        """Should create search result."""
        result = SearchResult(
            file_path="test.py",
            start_line=1,
            end_line=10,
            content="def test(): pass",
            score=0.95,
        )
        assert result.file_path == "test.py"
        assert result.score == 0.95
        assert result.match_type == "hybrid"


class TestSearchResults:
    """Tests for SearchResults dataclass."""

    def test_create_empty(self):
        """Should create empty results."""
        results = SearchResults()
        assert results.results == []
        assert results.query_time_ms == 0.0
        assert results.total_chunks == 0
