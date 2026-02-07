"""Tests for CLI search and index subcommands."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lgrep.cli import _cmd_index, _cmd_search, main
from lgrep.indexing import IndexStatus
from lgrep.storage import SearchResult, SearchResults


class TestCLIDispatch:
    """Tests for CLI argument parsing and dispatch."""

    def test_main_version(self, capsys):
        """--version should print version and exit 0."""
        with patch("sys.argv", ["lgrep", "--version"]):
            rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "lgrep" in out

    def test_main_help(self, capsys):
        """--help should print help text and exit 0."""
        with patch("sys.argv", ["lgrep", "--help"]):
            rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "search" in out
        assert "index" in out
        assert "remove" in out

    def test_main_unknown_arg(self, capsys):
        """Unknown arguments should exit 1."""
        with patch("sys.argv", ["lgrep", "--bogus"]):
            rc = main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "Unknown" in err

    def test_main_dispatches_to_search(self):
        """'lgrep search' should dispatch to _cmd_search."""
        with (
            patch("sys.argv", ["lgrep", "search", "--help"]),
            patch("lgrep.cli._cmd_search", return_value=0) as mock_search,
        ):
            rc = main()
        assert rc == 0
        mock_search.assert_called_once_with(["--help"])

    def test_main_dispatches_to_index(self):
        """'lgrep index' should dispatch to _cmd_index."""
        with (
            patch("sys.argv", ["lgrep", "index", "--help"]),
            patch("lgrep.cli._cmd_index", return_value=0) as mock_index,
        ):
            rc = main()
        assert rc == 0
        mock_index.assert_called_once_with(["--help"])


class TestCmdSearchArgParsing:
    """Tests for _cmd_search argument parsing."""

    def test_search_help(self, capsys):
        """search --help should print search-specific help and exit 0."""
        rc = _cmd_search(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "query" in out
        assert "--limit" in out
        assert "--no-hybrid" in out

    def test_search_missing_query(self, capsys):
        """Missing query should print error to stderr and exit 1."""
        rc = _cmd_search([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "query is required" in err

    def test_search_unknown_option(self, capsys):
        """Unknown flags should exit 1."""
        rc = _cmd_search(["--bogus", "query"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Unknown option" in err

    def test_search_no_api_key(self, capsys, monkeypatch):
        """Missing VOYAGE_API_KEY should output JSON error and exit 1."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        rc = _cmd_search(["some query"])
        assert rc == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["error"] == "VOYAGE_API_KEY not set"

    def test_search_no_index(self, capsys, monkeypatch, tmp_path):
        """Non-existent index should output JSON error and exit 1."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        rc = _cmd_search(["some query", str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "No index found" in data["error"]


class TestCmdSearchExecution:
    """Tests for _cmd_search execution with mocked dependencies."""

    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_search_hybrid_default(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, capsys, monkeypatch
    ):
        """Default search should use hybrid mode."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        # Mock db path exists
        mock_db_path = MagicMock()
        mock_db_path.exists.return_value = True
        mock_get_path.return_value = mock_db_path

        # Mock embedder
        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_embedder_cls.return_value = mock_embedder

        # Mock store
        mock_store = MagicMock()
        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "def foo(): pass", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=50,
        )
        mock_store.search_hybrid.return_value = results
        mock_store_cls.return_value = mock_store

        rc = _cmd_search(["find auth", "/tmp/project"])
        assert rc == 0

        # Verify hybrid was called (not vector-only)
        mock_store.search_hybrid.assert_called_once()
        mock_store.search_vector.assert_not_called()

        # Verify JSON output
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data["results"]) == 1
        assert data["results"][0]["file_path"] == "a.py"
        assert data["query_time_ms"] == 5.0

    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_search_vector_only(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, capsys, monkeypatch
    ):
        """--no-hybrid should use vector-only search."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_db_path.exists.return_value = True
        mock_get_path.return_value = mock_db_path

        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_embedder_cls.return_value = mock_embedder

        mock_store = MagicMock()
        results = SearchResults(
            results=[SearchResult("b.py", 5, 15, "class Bar:", 0.85, "vector")],
            query_time_ms=3.0,
            total_chunks=50,
        )
        mock_store.search_vector.return_value = results
        mock_store_cls.return_value = mock_store

        rc = _cmd_search(["find auth", "/tmp/project", "--no-hybrid"])
        assert rc == 0

        mock_store.search_vector.assert_called_once()
        mock_store.search_hybrid.assert_not_called()

    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_search_custom_limit(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, capsys, monkeypatch
    ):
        """-m N should pass custom limit to search."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_db_path.exists.return_value = True
        mock_get_path.return_value = mock_db_path

        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_embedder_cls.return_value = mock_embedder

        mock_store = MagicMock()
        results = SearchResults(results=[], query_time_ms=1.0, total_chunks=0)
        mock_store.search_hybrid.return_value = results
        mock_store_cls.return_value = mock_store

        rc = _cmd_search(["-m", "5", "query", "/tmp/project"])
        assert rc == 0

        # Verify limit was passed
        call_args = mock_store.search_hybrid.call_args
        assert call_args[0][2] == 5  # third positional arg is limit

    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_search_defaults_to_cwd(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, capsys, monkeypatch
    ):
        """Omitting path should default to cwd."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_db_path.exists.return_value = True
        mock_get_path.return_value = mock_db_path

        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 1024
        mock_embedder_cls.return_value = mock_embedder

        mock_store = MagicMock()
        results = SearchResults(results=[], query_time_ms=1.0, total_chunks=0)
        mock_store.search_hybrid.return_value = results
        mock_store_cls.return_value = mock_store

        rc = _cmd_search(["some query"])
        assert rc == 0

        # get_project_db_path should have been called with cwd
        call_arg = mock_get_path.call_args[0][0]
        assert call_arg == Path.cwd().resolve()

    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_search_exception_returns_json_error(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, capsys, monkeypatch
    ):
        """Exceptions during search should be caught and returned as JSON."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_db_path.exists.return_value = True
        mock_get_path.return_value = mock_db_path

        mock_embedder = MagicMock()
        mock_embedder.embed_query.side_effect = RuntimeError("API timeout")
        mock_embedder_cls.return_value = mock_embedder

        rc = _cmd_search(["some query", "/tmp/project"])
        assert rc == 1

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "API timeout" in data["error"]


class TestCmdIndexArgParsing:
    """Tests for _cmd_index argument parsing."""

    def test_index_help(self, capsys):
        """index --help should print index-specific help and exit 0."""
        rc = _cmd_index(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "path" in out
        assert "--chunk-size" in out

    def test_index_unknown_option(self, capsys):
        """Unknown flags should exit 1."""
        rc = _cmd_index(["--bogus"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Unknown option" in err

    def test_index_invalid_path(self, capsys):
        """Non-existent path should output JSON error and exit 1."""
        rc = _cmd_index(["/tmp/nonexistent_lgrep_test_path"])
        assert rc == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "does not exist" in data["error"]

    def test_index_no_api_key(self, capsys, monkeypatch, tmp_path):
        """Missing VOYAGE_API_KEY should output JSON error and exit 1."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        rc = _cmd_index([str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["error"] == "VOYAGE_API_KEY not set"


class TestCmdIndexExecution:
    """Tests for _cmd_index execution with mocked dependencies."""

    @patch("lgrep.indexing.Indexer")
    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_index_success(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, mock_indexer_cls,
        capsys, monkeypatch, tmp_path
    ):
        """Successful index should print JSON status and exit 0."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_get_path.return_value = mock_db_path

        mock_indexer = MagicMock()
        mock_indexer.index_all.return_value = IndexStatus(
            file_count=10, chunk_count=50, duration_ms=1234.56, total_tokens=5000
        )
        mock_indexer_cls.return_value = mock_indexer

        rc = _cmd_index([str(tmp_path)])
        assert rc == 0

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["file_count"] == 10
        assert data["chunk_count"] == 50
        assert data["duration_ms"] == 1234.56
        assert data["total_tokens"] == 5000
        assert data["project"] == str(tmp_path.resolve())

    @patch("lgrep.indexing.Indexer")
    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_index_custom_chunk_size(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, mock_indexer_cls,
        capsys, monkeypatch, tmp_path
    ):
        """--chunk-size N should pass custom chunk size to Indexer."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_get_path.return_value = mock_db_path

        mock_indexer = MagicMock()
        mock_indexer.index_all.return_value = IndexStatus()
        mock_indexer_cls.return_value = mock_indexer

        rc = _cmd_index(["--chunk-size", "250", str(tmp_path)])
        assert rc == 0

        # Verify chunk_size was passed to Indexer
        call_kwargs = mock_indexer_cls.call_args
        assert call_kwargs[1]["chunk_size"] == 250

    @patch("lgrep.indexing.Indexer")
    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_index_defaults_to_cwd(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, mock_indexer_cls,
        capsys, monkeypatch
    ):
        """Omitting path should default to cwd."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_get_path.return_value = mock_db_path

        mock_indexer = MagicMock()
        mock_indexer.index_all.return_value = IndexStatus()
        mock_indexer_cls.return_value = mock_indexer

        rc = _cmd_index([])
        assert rc == 0

        # Indexer should have been called with cwd
        call_args = mock_indexer_cls.call_args[0]
        assert call_args[0] == Path.cwd().resolve()

    @patch("lgrep.indexing.Indexer")
    @patch("lgrep.embeddings.VoyageEmbedder")
    @patch("lgrep.storage.ChunkStore")
    @patch("lgrep.storage.get_project_db_path")
    def test_index_exception_returns_json_error(
        self, mock_get_path, mock_store_cls, mock_embedder_cls, mock_indexer_cls,
        capsys, monkeypatch, tmp_path
    ):
        """Exceptions during indexing should be caught and returned as JSON."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        mock_db_path = MagicMock()
        mock_get_path.return_value = mock_db_path

        mock_indexer = MagicMock()
        mock_indexer.index_all.side_effect = RuntimeError("Disk full")
        mock_indexer_cls.return_value = mock_indexer

        rc = _cmd_index([str(tmp_path)])
        assert rc == 1

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "Disk full" in data["error"]
