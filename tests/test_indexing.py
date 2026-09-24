"""Tests for the indexing logic."""

import hashlib
import uuid
from unittest.mock import MagicMock

import pytest

from lgrep.indexing import Indexer
from lgrep.storage import ChunkStore, CodeChunk


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
            model="voyage-code-4",
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


def _stored_row(file_path, chunk_index, content, file_hash):
    """A stored row with the collapsed 1/1 range the repair fixes."""
    return CodeChunk(
        id=str(uuid.uuid4()),
        file_path=file_path,
        chunk_index=chunk_index,
        start_line=1,
        end_line=1,
        content=content,
        vector=[0.1] * 1024,
        file_hash=file_hash,
        indexed_at=1.0,
        embedding_model="voyage-code-4",
    )


def _stored_rows(store, file_path):
    """Every stored column for one file's rows, in chunk order."""
    rows = store.table.search().where(f"file_path = '{file_path}'").to_list()
    return sorted(rows, key=lambda r: r["chunk_index"])


def _no_embed_embedder():
    """An embedder whose use fails the test — the repair never embeds."""
    embedder = MagicMock()
    embedder.embed_documents.side_effect = AssertionError("repair must not embed")
    embedder.embed_query.side_effect = AssertionError("repair must not embed")
    return embedder


class TestLineRangeRepair:
    """repair_line_ranges fixes stored rows of unchanged files, no embeddings."""

    SOURCE = (
        '"""Module."""\n'  # 1
        "\n"  # 2
        "class Svc:\n"  # 3
        "    def run(self):\n"  # 4
        '        return "ok"\n'  # 5
        "\n"  # 6
        "    def stop(self):\n"  # 7
        '        return "done"\n'  # 8
    )

    def _write_source(self, tmp_path, name="svc.py"):
        src = tmp_path / name
        src.write_text(self.SOURCE)
        return src

    def _store_with_collapsed_rows(self, tmp_path, file_hash):
        store = ChunkStore(tmp_path / "cache")
        store.add_chunks(
            [
                # First split chunk: header + blank line + lstripped body.
                _stored_row(
                    "svc.py",
                    0,
                    'class Svc:\n\ndef run(self):\n        return "ok"',
                    file_hash,
                ),
                # Later split chunk: header + breadcrumb + lstripped body.
                _stored_row(
                    "svc.py",
                    1,
                    'class Svc:\n\n\t...\n\ndef stop(self):\n        return "done"',
                    file_hash,
                ),
            ]
        )
        return store

    def test_repairs_unchanged_file_rows_without_embedding(self, tmp_path):
        self._write_source(tmp_path)
        file_hash = hashlib.sha256((tmp_path / "svc.py").read_bytes()).hexdigest()
        store = self._store_with_collapsed_rows(tmp_path, file_hash)

        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())

        assert indexer.repair_line_ranges() == 2

        rows = {r["chunk_index"]: r for r in _stored_rows(store, "svc.py")}
        assert (rows[0]["start_line"], rows[0]["end_line"]) == (4, 5)
        assert (rows[1]["start_line"], rows[1]["end_line"]) == (7, 8)
        assert store.line_repair_done() is True

    def test_repair_preserves_stored_vectors(self, tmp_path):
        """Rows are rewritten in place; embeddings are never recomputed."""
        self._write_source(tmp_path)
        file_hash = hashlib.sha256((tmp_path / "svc.py").read_bytes()).hexdigest()
        store = self._store_with_collapsed_rows(tmp_path, file_hash)

        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())
        indexer.repair_line_ranges()

        rows = _stored_rows(store, "svc.py")
        for row in rows:
            assert row["vector"] == pytest.approx([0.1] * 1024)
            assert row["content"].startswith("class Svc:")

    def test_skips_rows_of_changed_files(self, tmp_path):
        """A file whose on-disk hash differs from the stored hash is left
        for the normal incremental re-chunk."""
        self._write_source(tmp_path)
        stale_hash = hashlib.sha256(b"older content").hexdigest()
        store = self._store_with_collapsed_rows(tmp_path, stale_hash)

        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())

        assert indexer.repair_line_ranges() == 0
        rows = _stored_rows(store, "svc.py")
        assert all(r["start_line"] == 1 and r["end_line"] == 1 for r in rows)
        # The pass still completes and records itself as done.
        assert store.line_repair_done() is True

    def test_second_run_is_a_noop_after_completion(self, tmp_path):
        self._write_source(tmp_path)
        file_hash = hashlib.sha256((tmp_path / "svc.py").read_bytes()).hexdigest()
        store = self._store_with_collapsed_rows(tmp_path, file_hash)

        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())

        assert indexer.repair_line_ranges() == 2
        assert indexer.repair_line_ranges() == 0

    def test_repairs_every_unchanged_file_in_one_pass(self, tmp_path):
        """Rows of several files are grouped by file and repaired together."""
        for name in ("svc.py", "other.py"):
            self._write_source(tmp_path, name)
        file_hash = hashlib.sha256((tmp_path / "svc.py").read_bytes()).hexdigest()
        store = self._store_with_collapsed_rows(tmp_path, file_hash)
        store.add_chunks(
            [
                _stored_row(
                    "other.py",
                    0,
                    'class Svc:\n\n\t...\n\ndef stop(self):\n        return "done"',
                    file_hash,
                )
            ]
        )

        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())

        assert indexer.repair_line_ranges() == 3
        (other,) = _stored_rows(store, "other.py")
        assert (other["start_line"], other["end_line"]) == (7, 8)

    def test_empty_cache_records_completion(self, tmp_path):
        store = ChunkStore(tmp_path / "cache")
        indexer = Indexer(project_path=tmp_path, storage=store, embedder=_no_embed_embedder())

        assert indexer.repair_line_ranges() == 0
        assert store.line_repair_done() is True
