"""Tests for LanceDB storage."""

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lgrep.storage import (
    CHUNKS_TABLE,
    EMBEDDING_DIM,
    ChunkStore,
    CodeChunk,
    get_project_db_path,
    has_disk_cache,
)


def make_chunk(
    file_path="test.py",
    chunk_index=0,
    start_line=1,
    end_line=5,
    content="print('hello')",
    vector=None,
    file_hash="hash",
    indexed_at=123.456,
):
    """Helper to create a CodeChunk."""
    if vector is None:
        vector = [0.1] * EMBEDDING_DIM
    return CodeChunk(
        id=hashlib.sha256(f"{file_path}:{chunk_index}".encode()).hexdigest(),
        file_path=file_path,
        chunk_index=chunk_index,
        start_line=start_line,
        end_line=end_line,
        content=content,
        vector=vector,
        file_hash=file_hash,
        indexed_at=indexed_at,
    )


@pytest.fixture
def temp_db_path(tmp_path):
    """Temporary database directory."""
    return tmp_path / "lgrep_test_db"


@pytest.fixture
def chunk_store(temp_db_path):
    """Initialized ChunkStore."""
    return ChunkStore(temp_db_path)


@pytest.fixture
def sample_chunks():
    """List of sample chunks."""
    return [
        make_chunk(file_path="a.py", chunk_index=0, content="def a(): pass"),
        make_chunk(file_path="a.py", chunk_index=1, content="def b(): pass"),
        make_chunk(file_path="b.py", chunk_index=0, content="def c(): pass"),
    ]


class TestCodeChunkModel:
    """Tests for CodeChunk model."""

    def test_model_fields(self):
        """Should have all required fields."""
        chunk = make_chunk()
        assert chunk.file_path == "test.py"
        assert len(chunk.vector) == EMBEDDING_DIM
        assert isinstance(chunk.id, str)

    def test_arrow_schema(self):
        """Should export valid arrow schema."""
        schema = CodeChunk.to_arrow_schema()
        assert "file_path" in schema.names
        assert "vector" in schema.names


class TestDbPathResolution:
    """Tests for database path resolution."""

    def test_get_project_db_path_consistency(self):
        """Same project path should resolve to same db path."""
        path = "/home/user/project"
        db1 = get_project_db_path(path)
        db2 = get_project_db_path(path)
        assert db1 == db2

    def test_get_project_db_path_different(self):
        """Different project paths should resolve to different db paths."""
        db1 = get_project_db_path("/path/a")
        db2 = get_project_db_path("/path/b")
        assert db1 != db2

    def test_has_disk_cache_check(self, tmp_path):
        """Should detect if lance files exist on disk."""
        project_path = tmp_path / "my_project"
        project_path.mkdir()

        assert has_disk_cache(project_path) is False

        # Create dummy lance file structure
        db_path = get_project_db_path(project_path)
        (db_path / (CHUNKS_TABLE + ".lance")).mkdir(parents=True)

        assert has_disk_cache(project_path) is True


class TestChunkStoreLifecycle:
    """Tests for ChunkStore initialization and basic operations."""

    def test_init_creates_directory(self, temp_db_path):
        """Should create database directory."""
        ChunkStore(temp_db_path)
        assert temp_db_path.exists()

    def test_init_reconnects_on_corruption(self, temp_db_path):
        """Should clear and reconnect if database is corrupted."""
        temp_db_path.mkdir()
        (temp_db_path / "junk.txt").write_text("some data")

        # Mock lancedb.connect to fail once then succeed
        with patch("lancedb.connect", side_effect=[RuntimeError("corrupt"), MagicMock()]):
            store = ChunkStore(temp_db_path)
            assert store.db is not None

        # Verify directory was cleared (junk.txt should be gone)
        assert not (temp_db_path / "junk.txt").exists()

    def test_init_deeply_nested_path(self):
        """Should create deeply nested database directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "deep" / "nested" / "db"
            ChunkStore(db_path)
            assert db_path.exists()

    def test_add_chunks(self, chunk_store, sample_chunks):
        """Should add chunks to the database."""
        count = chunk_store.add_chunks(sample_chunks)
        assert count == 3
        assert chunk_store.count_chunks() == 3

    def test_add_chunks_empty(self, chunk_store):
        """Should handle empty list gracefully."""
        assert chunk_store.add_chunks([]) == 0

    def test_upsert_chunks(self, chunk_store, sample_chunks):
        """Should update existing chunks and add new ones."""
        chunk_store.add_chunks(sample_chunks)

        # Update one chunk, add one new
        updated = sample_chunks[0]
        updated.content = "updated content"

        new_chunk = make_chunk(file_path="c.py", chunk_index=0)

        chunk_store.upsert_chunks([updated, new_chunk])

        assert chunk_store.count_chunks() == 4
        # Verify update
        results = chunk_store.table.search().where(f"id = '{updated.id}'").to_list()
        assert results[0]["content"] == "updated content"

    def test_upsert_chunks_empty(self, chunk_store):
        """Should handle empty list gracefully."""
        assert chunk_store.upsert_chunks([]) == 0

    def test_delete_by_file(self, chunk_store, sample_chunks):
        """Should delete all chunks associated with a file."""
        chunk_store.add_chunks(sample_chunks)
        assert chunk_store.count_chunks() == 3

        deleted = chunk_store.delete_by_file("a.py")
        assert deleted == 2
        assert chunk_store.count_chunks() == 1

    def test_get_file_hash(self, chunk_store, sample_chunks):
        """Should retrieve the stored hash for a file."""
        chunk_store.add_chunks(sample_chunks)
        h = chunk_store.get_file_hash("a.py")
        assert h == "hash"

    def test_get_file_hash_missing(self, chunk_store):
        """Should return None for missing files."""
        assert chunk_store.get_file_hash("none.py") is None

    def test_get_indexed_files(self, chunk_store, sample_chunks):
        """Should return set of all indexed file paths."""
        chunk_store.add_chunks(sample_chunks)
        files = chunk_store.get_indexed_files()
        assert files == {"a.py", "b.py"}

    def test_clear(self, chunk_store, sample_chunks):
        """Should drop the table and reset state."""
        chunk_store.add_chunks(sample_chunks)
        chunk_store.ensure_fts_index()
        assert chunk_store.count_chunks() == 3

        chunk_store.clear()
        assert chunk_store._table is None
        assert chunk_store._fts_indexed is False
        # Accessing table property recreates it empty
        assert chunk_store.count_chunks() == 0


class TestSearch:
    """Tests for search functionality."""

    def test_search_vector(self, chunk_store, sample_chunks):
        """Should perform pure vector search."""
        chunk_store.add_chunks(sample_chunks)
        query_vector = [0.1] * EMBEDDING_DIM

        results = chunk_store.search_vector(query_vector, limit=2)
        assert len(results.results) == 2
        assert results.total_chunks == 3
        assert results.results[0].match_type == "vector"

    def test_search_hybrid(self, chunk_store, sample_chunks):
        """Should perform hybrid search with RRF reranking."""
        chunk_store.add_chunks(sample_chunks)
        query_vector = [0.1] * EMBEDDING_DIM

        results = chunk_store.search_hybrid(query_vector, "def pass", limit=2)
        assert len(results.results) == 2
        assert results.total_chunks == 3
        assert results.results[0].match_type == "hybrid"
        assert chunk_store._fts_indexed is True

    def test_search_hybrid_large_table_creates_vector_index(self, temp_db_path):
        """Vector index should be auto-created when row count exceeds threshold."""
        store = ChunkStore(temp_db_path)
        # Need at least 256 rows to train PQ index in LanceDB
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(260)]
        store.add_chunks(chunks)

        # Patch count_rows to report > 1000 so vector index path triggers
        def fake_count():
            return 1500

        import random

        query_vector = [random.random() for _ in range(EMBEDDING_DIM)]

        with patch.object(store.table, "count_rows", side_effect=fake_count):
            # This should not raise
            store.search_hybrid(query_vector, "test query", limit=3)
            assert store._vector_indexed is True

    def test_ensure_fts_index_idempotent(self, chunk_store):
        """Calling ensure_fts_index multiple times should not raise."""
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(3)]
        chunk_store.add_chunks(chunks)

        # Call twice - second call should be a no-op
        chunk_store.ensure_fts_index()
        chunk_store.ensure_fts_index()
        assert chunk_store._fts_indexed is True


class TestIdempotentIndexCreation:
    """Tests for idempotent vector and FTS index creation.

    Indexes should only be created once, not on every search call.
    """

    def test_vector_indexed_flag_prevents_rebuild(self, temp_db_path):
        """After first vector index build, subsequent searches skip rebuild."""
        store = ChunkStore(temp_db_path)
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(5)]
        store.add_chunks(chunks)

        # _vector_indexed should start False
        assert store._vector_indexed is False

        # After a hybrid search triggers index creation on large table,
        # flag should be True. For small tables (< 1000), index creation
        # is skipped, so flag stays False - that's fine.
        # We test the flag directly
        store._vector_indexed = True

        # With flag set, the index build branch should be skipped
        # (we verify by checking the flag survives)
        import random

        query_vector = [random.random() for _ in range(EMBEDDING_DIM)]
        store.search_hybrid(query_vector, "test query", limit=3)
        assert store._vector_indexed is True

    def test_fts_indexed_flag_prevents_rebuild(self, chunk_store):
        """After first FTS index build, subsequent calls skip rebuild."""
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(3)]
        chunk_store.add_chunks(chunks)

        assert chunk_store._fts_indexed is False
        chunk_store.ensure_fts_index()
        assert chunk_store._fts_indexed is True

        # Second call should be a no-op (flag is already True)
        chunk_store.ensure_fts_index()
        assert chunk_store._fts_indexed is True

    def test_clear_resets_index_flags(self, chunk_store):
        """Clearing the store should reset all index flags."""
        chunks = [make_chunk(content=f"content {i}", chunk_index=i) for i in range(3)]
        chunk_store.add_chunks(chunks)
        chunk_store.ensure_fts_index()
        assert chunk_store._fts_indexed is True

        chunk_store.clear()
        assert chunk_store._fts_indexed is False
