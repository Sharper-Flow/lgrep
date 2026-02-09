"""Tests for the indexing logic."""

from unittest.mock import MagicMock

import pytest

from lgrep.indexing import Indexer
from lgrep.storage import ChunkStore


@pytest.fixture
def mock_embedder():
    """Create a mock VoyageEmbedder."""
    embedder = MagicMock()

    # Mock embed_documents to return embeddings for any list of texts
    def side_effect(texts, **kwargs):
        from lgrep.embeddings import EmbeddingResult

        return EmbeddingResult(
            embeddings=[[0.1] * 1024 for _ in texts],
            token_usage=len(texts) * 10,
            model="voyage-code-3",
        )

    embedder.embed_documents.side_effect = side_effect
    return embedder


@pytest.fixture
def mock_storage():
    """Create a mock ChunkStore."""
    return MagicMock(spec=ChunkStore)


class TestIndexer:
    """Tests for Indexer class."""

    def test_index_directory(self, tmp_path, mock_embedder, mock_storage):
        """Should index all files in a directory."""
        # Create dummy files
        (tmp_path / "a.py").write_text("def a(): pass")
        (tmp_path / "b.py").write_text("def b(): pass")

        indexer = Indexer(
            project_path=tmp_path,
            storage=mock_storage,
            embedder=mock_embedder,
        )

        status = indexer.index_all()

        assert status.file_count == 2
        assert status.chunk_count > 0
        assert status.duration_ms > 0

        # Verify storage.add_chunks was called
        assert mock_storage.add_chunks.called

        # Verify embedder.embed_documents was called
        assert mock_embedder.embed_documents.called

    def test_index_file_incremental(self, tmp_path, mock_embedder, mock_storage):
        """Should index a single file (incremental)."""
        file_path = tmp_path / "c.py"
        file_path.write_text("def c(): pass")

        indexer = Indexer(
            project_path=tmp_path,
            storage=mock_storage,
            embedder=mock_embedder,
        )

        status = indexer.index_file(file_path)

        assert status.file_count == 1
        assert status.chunk_count > 0

        # Should delete existing chunks for this file first
        mock_storage.delete_by_file.assert_called_with("c.py")
        assert mock_storage.add_chunks.called

    def test_index_file_skips_if_hash_matches(self, tmp_path, mock_embedder, mock_storage):
        """Should skip indexing if file hash hasn't changed."""
        file_path = tmp_path / "unchanged.py"
        file_path.write_text("def unchanged(): pass")

        indexer = Indexer(
            project_path=tmp_path,
            storage=mock_storage,
            embedder=mock_embedder,
        )

        # Mock storage to return a matching hash
        import hashlib

        expected_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        mock_storage.get_file_hash.return_value = expected_hash

        status = indexer.index_file(file_path)

        assert status.file_count == 1
        # Should not have called embedder or storage for new chunks
        assert not mock_embedder.embed_documents.called
        assert not mock_storage.add_chunks.called
        # But should have checked the hash
        mock_storage.get_file_hash.assert_called_with("unchanged.py")
