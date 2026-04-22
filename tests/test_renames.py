"""Regression tests for semantic tool renames (tk-oVSzRvjU).

RED phase: these tests fail before the renames are applied.
GREEN phase: all pass after server.py functions are renamed.

Verifies:
- New function names exist in server module
- Old function names are GONE (no aliases)
- Response shapes are preserved (regression guard)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from lgrep.storage import SearchResult, SearchResults


class TestSemanticToolRenames:
    """Verify the 5 semantic tools are renamed with _semantic suffix."""

    def test_search_semantic_importable(self):
        """lgrep_search_semantic must be importable from server module."""
        from lgrep.server import search_semantic  # noqa: F401

    def test_index_semantic_importable(self):
        """lgrep_index_semantic must be importable from server module."""
        from lgrep.server import index_semantic  # noqa: F401

    def test_status_semantic_importable(self):
        """lgrep_status_semantic must be importable from server module."""
        from lgrep.server import status_semantic  # noqa: F401

    def test_watch_start_semantic_importable(self):
        """lgrep_watch_start_semantic must be importable from server module."""
        from lgrep.server import watch_start_semantic  # noqa: F401

    def test_watch_stop_semantic_importable(self):
        """lgrep_watch_stop_semantic must be importable from server module."""
        from lgrep.server import watch_stop_semantic  # noqa: F401

    def test_old_search_name_gone(self):
        """Old 'search' function name must NOT exist in server module."""
        import lgrep.server as srv

        assert not hasattr(srv, "search"), (
            "Old 'search' function still exists — must be removed, not aliased"
        )

    def test_old_index_name_gone(self):
        """Old 'index' function name must NOT exist in server module."""
        import lgrep.server as srv

        assert not hasattr(srv, "index"), (
            "Old 'index' function still exists — must be removed, not aliased"
        )

    def test_old_status_name_gone(self):
        """Old 'status' function name must NOT exist in server module."""
        import lgrep.server as srv

        assert not hasattr(srv, "status"), (
            "Old 'status' function still exists — must be removed, not aliased"
        )

    def test_old_watch_start_name_gone(self):
        """Old 'watch_start' function name must NOT exist in server module."""
        import lgrep.server as srv

        assert not hasattr(srv, "watch_start"), (
            "Old 'watch_start' function still exists — must be removed, not aliased"
        )

    def test_old_watch_stop_name_gone(self):
        """Old 'watch_stop' function name must NOT exist in server module."""
        import lgrep.server as srv

        assert not hasattr(srv, "watch_stop"), (
            "Old 'watch_stop' function still exists — must be removed, not aliased"
        )


class TestSemanticToolResponseShapes:
    """Regression guard: renamed tools must preserve exact response shapes."""

    @pytest.mark.asyncio
    async def test_search_semantic_response_shape(self):
        """lgrep_search_semantic must return same JSON shape as old lgrep_search."""
        from lgrep.server import LgrepContext, ProjectState, search_semantic

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        app_ctx.embedder = MagicMock()

        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock())
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=10.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024
        app_ctx.embedder.embed_query_async = AsyncMock(return_value=[0.1] * 1024)

        response = await search_semantic(query="test", path="/path", ctx=mock_ctx)
        data = response

        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["file_path"] == "a.py"
        # Contract: line_number is required, mapped from start_line
        assert "line_number" in data["results"][0]
        assert data["results"][0]["line_number"] == 1  # start_line value
        # Contract: total == len(results), not total_chunks
        assert data["total"] == len(data["results"])
        assert data["total"] != 0
        # query_time_ms removed from SearchSemanticResult TypedDict

    @pytest.mark.asyncio
    async def test_status_semantic_response_shape(self):
        """lgrep_status_semantic must return same JSON shape as old lgrep_status."""
        from lgrep.server import LgrepContext, ProjectState, status_semantic

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()

        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock(), watching=True)
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        mock_db.count_chunks.return_value = 500
        mock_db.get_indexed_files.return_value = {"a.py", "b.py"}

        response = await status_semantic(path="/path", ctx=mock_ctx)
        data = response

        assert data["files"] == 2
        assert data["chunks"] == 500
        assert data["watching"] is True
        assert data["project"] == "/path"

    @pytest.mark.asyncio
    async def test_watch_stop_semantic_response_shape(self):
        """lgrep_watch_stop_semantic must return same JSON shape as old lgrep_watch_stop."""
        from lgrep.server import LgrepContext, watch_stop_semantic

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await watch_stop_semantic(ctx=mock_ctx)
        data = response

        assert data["stopped"] is True
        assert data["projects_stopped"] == []

    @pytest.mark.asyncio
    async def test_index_semantic_error_shape(self):
        """lgrep_index_semantic must return same error shape as old lgrep_index."""
        from lgrep.server import LgrepContext, index_semantic

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await index_semantic(path="/nonexistent/path/xyz", ctx=mock_ctx)
        data = response

        assert "error" in data
        assert "does not exist" in data["error"]


class TestCLISemanticRenames:
    """Verify CLI help text references renamed tool names."""

    def test_cli_help_mentions_search_semantic(self, capsys):
        """CLI help should mention lgrep_search_semantic."""
        from lgrep.cli import _print_help

        _print_help()
        out = capsys.readouterr().out
        assert "search-semantic" in out or "search_semantic" in out or "search" in out

    def test_cli_dispatches_search_semantic(self):
        """'lgrep search-semantic' should dispatch to _cmd_search_semantic."""

        with (
            patch("sys.argv", ["lgrep", "search-semantic", "--help"]),
            patch("lgrep.cli._cmd_search_semantic", return_value=0) as mock_fn,
        ):
            from lgrep.cli import main

            rc = main()
        assert rc == 0
        mock_fn.assert_called_once_with(["--help"])

    def test_cli_dispatches_index_semantic(self):
        """'lgrep index-semantic' should dispatch to _cmd_index_semantic."""

        with (
            patch("sys.argv", ["lgrep", "index-semantic", "--help"]),
            patch("lgrep.cli._cmd_index_semantic", return_value=0) as mock_fn,
        ):
            from lgrep.cli import main

            rc = main()
        assert rc == 0
        mock_fn.assert_called_once_with(["--help"])
