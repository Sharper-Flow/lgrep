"""Tests for CLI search and index subcommands."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from lgrep.cli import _cmd_gc, _cmd_init_ignore, _cmd_prune_symbols, main
from lgrep.cli import _cmd_index_semantic as _cmd_index
from lgrep.cli import _cmd_prune_orphans as _cmd_prune_orphans
from lgrep.cli import _cmd_search_semantic as _cmd_search
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
        assert "search-semantic" in out
        assert "index-semantic" in out
        assert "remove" in out

    def test_main_unknown_arg(self, capsys):
        """Unknown arguments should exit 1."""
        with patch("sys.argv", ["lgrep", "--bogus"]):
            rc = main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "Unknown" in err

    def test_main_dispatches_to_search(self):
        """'lgrep search-semantic' should dispatch to _cmd_search_semantic."""
        with (
            patch("sys.argv", ["lgrep", "search-semantic", "--help"]),
            patch("lgrep.cli._cmd_search_semantic", return_value=0) as mock_search,
        ):
            rc = main()
        assert rc == 0
        mock_search.assert_called_once_with(["--help"])

    def test_main_dispatches_to_index(self):
        """'lgrep index-semantic' should dispatch to _cmd_index_semantic."""
        with (
            patch("sys.argv", ["lgrep", "index-semantic", "--help"]),
            patch("lgrep.cli._cmd_index_semantic", return_value=0) as mock_index,
        ):
            rc = main()
        assert rc == 0
        mock_index.assert_called_once_with(["--help"])

    def test_main_dispatches_to_init_ignore(self):
        """'lgrep init-ignore' should dispatch to _cmd_init_ignore."""
        with (
            patch("sys.argv", ["lgrep", "init-ignore", "--help"]),
            patch("lgrep.cli._cmd_init_ignore", return_value=0) as mock_init,
        ):
            rc = main()
        assert rc == 0
        mock_init.assert_called_once_with(["--help"])

    def test_main_dispatches_to_prune_orphans(self):
        """'lgrep prune-orphans' should dispatch to _cmd_prune_orphans."""
        with (
            patch("sys.argv", ["lgrep", "prune-orphans", "--help"]),
            patch("lgrep.cli._cmd_prune_orphans", return_value=0) as mock_prune,
        ):
            rc = main()
        assert rc == 0
        mock_prune.assert_called_once_with(["--help"])

    def test_main_server_defaults_to_stdio(self):
        """No subcommand should start MCP server with stdio defaults."""
        with (
            patch("sys.argv", ["lgrep"]),
            patch("lgrep.server.run_server", return_value=0) as mock_run,
        ):
            rc = main()

        assert rc == 0
        mock_run.assert_called_once_with(transport="stdio", host="127.0.0.1", port=6285)

    def test_main_server_streamable_http(self):
        """CLI should support streamable-http transport with host/port options."""
        with (
            patch(
                "sys.argv",
                [
                    "lgrep",
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "6388",
                ],
            ),
            patch("lgrep.server.run_server", return_value=0) as mock_run,
        ):
            rc = main()

        assert rc == 0
        mock_run.assert_called_once_with(transport="streamable-http", host="127.0.0.1", port=6388)

    def test_main_invalid_transport(self, capsys):
        """Invalid transport should return a validation error."""
        with patch("sys.argv", ["lgrep", "--transport", "http"]):
            rc = main()

        assert rc == 1
        err = capsys.readouterr().err
        assert "Invalid transport" in err


class TestCmdSearchArgParsing:
    """Tests for _cmd_search argument parsing."""

    def test_search_help(self, capsys):
        """search-semantic --help should print search-specific help and exit 0."""
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
        self,
        mock_get_path,
        mock_store_cls,
        mock_embedder_cls,
        mock_indexer_cls,
        capsys,
        monkeypatch,
        tmp_path,
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
        self,
        mock_get_path,
        mock_store_cls,
        mock_embedder_cls,
        mock_indexer_cls,
        capsys,
        monkeypatch,
        tmp_path,
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
        self,
        mock_get_path,
        mock_store_cls,
        mock_embedder_cls,
        mock_indexer_cls,
        capsys,
        monkeypatch,
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
        self,
        mock_get_path,
        mock_store_cls,
        mock_embedder_cls,
        mock_indexer_cls,
        capsys,
        monkeypatch,
        tmp_path,
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


class TestCmdInitIgnore:
    def test_init_ignore_help(self, capsys):
        rc = _cmd_init_ignore(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "init-ignore" in out
        assert "--force" in out

    def test_init_ignore_creates_file(self, tmp_path, capsys):
        rc = _cmd_init_ignore([str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["created"] is True
        assert (tmp_path / ".lgrepignore").exists()

    def test_init_ignore_no_overwrite_without_force(self, tmp_path, capsys):
        ignore_file = tmp_path / ".lgrepignore"
        ignore_file.write_text("custom\n")

        rc = _cmd_init_ignore([str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["created"] is False
        assert ignore_file.read_text() == "custom\n"

    def test_init_ignore_force_overwrites(self, tmp_path, capsys):
        ignore_file = tmp_path / ".lgrepignore"
        ignore_file.write_text("custom\n")

        rc = _cmd_init_ignore(["--force", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["created"] is True
        assert ignore_file.read_text() != "custom\n"


class TestCmdPruneOrphans:
    def test_prune_help(self, capsys):
        rc = _cmd_prune_orphans(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "prune-orphans" in out
        assert "--execute" in out

    @patch("lgrep.tools.prune_orphans.prune_orphans")
    def test_prune_dry_run_default(self, mock_prune, capsys, tmp_path):
        mock_prune.return_value = {
            "dry_run": True,
            "dirs_examined": 0,
            "orphans": [],
            "skipped_active": [],
            "deleted_dirs": 0,
            "reclaimed_bytes": 0,
            "failures": [],
            "_meta": {},
        }

        rc = _cmd_prune_orphans(["--cache-dir", str(tmp_path)])

        assert rc == 0
        assert mock_prune.call_args.kwargs["dry_run"] is True

    @patch("lgrep.tools.prune_orphans.prune_orphans")
    def test_prune_execute_flag(self, mock_prune, capsys, tmp_path):
        mock_prune.return_value = {
            "dry_run": False,
            "dirs_examined": 1,
            "orphans": [],
            "skipped_active": [],
            "deleted_dirs": 1,
            "reclaimed_bytes": 1,
            "failures": [],
            "_meta": {},
        }

        rc = _cmd_prune_orphans(["--execute", "--cache-dir", str(tmp_path)])

        assert rc == 0
        assert mock_prune.call_args.kwargs["dry_run"] is False

    @patch("lgrep.tools.prune_orphans.prune_orphans")
    def test_prune_rejects_execute_and_dry_run_together(self, mock_prune, capsys):
        # Deletion is irreversible, so passing both flags must be an
        # error rather than "last flag wins".
        rc = _cmd_prune_orphans(["--execute", "--dry-run"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err
        # And the prune tool must not have been invoked.
        mock_prune.assert_not_called()


class TestGcSubcommand:
    """Tests for lgrep gc CLI subcommand."""

    def test_gc_help(self, capsys):
        """lgrep gc --help should print usage text."""
        rc = _cmd_gc(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "gc" in out
        assert "--execute" in out
        assert "--dry-run" in out

    def test_gc_dry_run_default(self, tmp_path, monkeypatch, capsys):
        """lgrep gc (no flags) runs prune_orphans with dry_run=True."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        with patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune:
            mock_prune.return_value = {
                "dry_run": True,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }
            rc = _cmd_gc([])

        assert rc == 0
        mock_prune.assert_called_once()
        assert mock_prune.call_args[1]["dry_run"] is True

    def test_gc_execute(self, tmp_path, monkeypatch, capsys):
        """lgrep gc --execute runs prune_orphans with dry_run=False."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        with patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune:
            mock_prune.return_value = {
                "dry_run": False,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }
            rc = _cmd_gc(["--execute"])

        assert rc == 0
        mock_prune.assert_called_once()
        assert mock_prune.call_args[1]["dry_run"] is False

    def test_gc_mutual_exclusion(self, capsys):
        """lgrep gc --execute --dry-run returns exit code 2."""
        with patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune:
            rc = _cmd_gc(["--execute", "--dry-run"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err
        mock_prune.assert_not_called()


class TestCmdPruneSymbols:
    """Tests for lgrep prune-symbols CLI subcommand."""

    def test_prune_symbols_help(self, capsys):
        """lgrep prune-symbols --help should print usage text."""
        rc = _cmd_prune_symbols(["--help"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "prune-symbols" in out
        assert "--execute" in out
        assert "--storage-dir" in out

    @patch("lgrep.tools.prune_symbols.prune_symbols")
    def test_prune_symbols_dry_run_default(self, mock_prune, capsys, tmp_path):
        """lgrep prune-symbols (no flags) runs dry_run and reports reclaim."""
        mock_prune.return_value = {
            "dry_run": True,
            "files_examined": 1,
            "stale_indexes": [],
            "skipped_active": [],
            "deleted_files": 0,
            "reclaimed_bytes": 42,
            "failures": [],
            "_meta": {},
        }

        rc = _cmd_prune_symbols(["--storage-dir", str(tmp_path)])
        out = capsys.readouterr().out
        data = json.loads(out)

        assert rc == 0
        assert mock_prune.call_args.kwargs["dry_run"] is True
        assert data["reclaimed_bytes"] == 42

    @patch("lgrep.tools.prune_symbols.prune_symbols")
    def test_prune_symbols_execute_flag(self, mock_prune, capsys, tmp_path):
        """lgrep prune-symbols --execute deletes with dry_run=False."""
        mock_prune.return_value = {
            "dry_run": False,
            "files_examined": 1,
            "stale_indexes": [],
            "skipped_active": [],
            "deleted_files": 1,
            "reclaimed_bytes": 42,
            "failures": [],
            "_meta": {},
        }

        rc = _cmd_prune_symbols(["--execute", "--storage-dir", str(tmp_path)])

        assert rc == 0
        assert mock_prune.call_args.kwargs["dry_run"] is False

    @patch("lgrep.tools.prune_symbols.prune_symbols")
    def test_prune_symbols_rejects_execute_and_dry_run_together(self, mock_prune, capsys):
        """--execute and --dry-run are mutually exclusive."""
        rc = _cmd_prune_symbols(["--execute", "--dry-run"])

        assert rc == 2
        err = capsys.readouterr().err
        assert "mutually exclusive" in err
        mock_prune.assert_not_called()

    def test_prune_symbols_execute_deletes_stale_index(self, tmp_path, monkeypatch, capsys):
        """lgrep prune-symbols --execute deletes a stale symbol index file."""
        monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "0")
        storage_dir = tmp_path / "symbols"
        storage_dir.mkdir()

        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        index_file = storage_dir / "index_1234567890abcdef.json"
        index_file.write_text(json.dumps({"repo_path": str(repo_path)}))

        # Move the index file outside the grace window so the default grace
        # check does not preserve it.
        old_time = 0
        index_file.touch()
        os.utime(index_file, (old_time, old_time))

        # Now the repo is gone, so the index is stale.
        repo_path.rmdir()

        rc = _cmd_prune_symbols(["--execute", "--storage-dir", str(storage_dir)])
        out = capsys.readouterr().out
        data = json.loads(out)

        assert rc == 0
        assert data["dry_run"] is False
        assert data["deleted_files"] == 1
        assert not index_file.exists()


class TestGcPruneSymbols:
    """Regression tests for the prune_symbols integration in gc."""

    def test_gc_combined_report_includes_prune_symbols(self, tmp_path, monkeypatch, capsys):
        """lgrep gc combined report contains prune_symbols alongside existing keys."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        symbol_report = {
            "dry_run": True,
            "files_examined": 0,
            "stale_indexes": [],
            "skipped_active": [],
            "deleted_files": 0,
            "reclaimed_bytes": 0,
            "failures": [],
            "_meta": {},
        }

        with (
            patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune,
            patch("lgrep.tools.prune_orphans.gc_worktree_meta") as mock_gc,
            patch("lgrep.tools.prune_symbols.prune_symbols") as mock_prune_symbols,
        ):
            mock_prune.return_value = {
                "dry_run": True,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }
            mock_gc.return_value = {"removed_aliases": 0, "examined": 0}
            mock_prune_symbols.return_value = symbol_report

            rc = _cmd_gc([])
            out = capsys.readouterr().out
            data = json.loads(out)

        assert rc == 0
        assert set(data.keys()) == {"prune_orphans", "gc_worktree_meta", "prune_symbols"}
        assert data["prune_symbols"] == symbol_report
        assert data["prune_orphans"] == mock_prune.return_value
        assert data["gc_worktree_meta"] == mock_gc.return_value

    def test_gc_preserves_existing_key_behavior(self, tmp_path, monkeypatch, capsys):
        """lgrep gc still passes dry_run through to prune_orphans and gc_worktree_meta."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        with (
            patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune,
            patch("lgrep.tools.prune_orphans.gc_worktree_meta") as mock_gc,
            patch("lgrep.tools.prune_symbols.prune_symbols") as mock_prune_symbols,
        ):
            mock_prune.return_value = {
                "dry_run": True,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }
            mock_gc.return_value = {"removed_aliases": 0, "examined": 0}
            mock_prune_symbols.return_value = {"reclaimed_bytes": 0}

            rc = _cmd_gc(["--execute"])

        assert rc == 0
        assert mock_prune.call_args[1]["dry_run"] is False
        assert mock_gc.call_args[1]["dry_run"] is False
        assert mock_prune_symbols.call_args[1]["dry_run"] is False

    def test_gc_dispatch_prune_symbols(self):
        """main() dispatches 'prune-symbols' subcommand."""
        with (
            patch("sys.argv", ["lgrep", "prune-symbols", "--help"]),
            patch("lgrep.cli._cmd_prune_symbols", return_value=0) as mock_prune,
        ):
            rc = main()
        assert rc == 0
        mock_prune.assert_called_once_with(["--help"])
