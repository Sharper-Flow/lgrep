"""Tests for server tool responses."""

import json
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import (
    lgrep_index,
    lgrep_search,
    lgrep_status,
    lgrep_watch_start,
    lgrep_watch_stop,
    remove_project,
    LgrepContext,
    ProjectState,
    MAX_PROJECTS,
    _ensure_project_initialized,
    _stop_watcher,
    _shutdown,
)
from lgrep.storage import SearchResult, SearchResults


class TestServerTools:
    """Tests for MCP tools in server.py."""

    @pytest.mark.asyncio
    async def test_lgrep_search_format(self):
        """Should format search results as JSON."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        app_ctx.embedder = MagicMock()

        # Set up a ProjectState in the projects dict
        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock())
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        # Mock storage return
        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=10.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        response = await lgrep_search(query="test", path="/path", ctx=mock_ctx)
        data = json.loads(response)

        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["file_path"] == "a.py"
        assert data["query_time_ms"] == 10.0

    @pytest.mark.asyncio
    async def test_lgrep_status_format(self):
        """Should format status as JSON."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()

        # Set up a ProjectState in the projects dict
        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock(), watching=True)
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        mock_db.count_chunks.return_value = 500
        mock_db.get_indexed_files.return_value = {"a.py", "b.py"}

        response = await lgrep_status(path="/path", ctx=mock_ctx)
        data = json.loads(response)

        assert data["files"] == 2
        assert data["chunks"] == 500
        assert data["watching"] is True
        assert data["project"] == "/path"


class TestServerErrorPaths:
    """Tests for error handling in MCP tools."""

    @pytest.mark.asyncio
    async def test_lgrep_search_no_index(self):
        """Should return error when no project is indexed."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()  # No projects in dict
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_search(query="test", path="/some/path", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "lgrep_index" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_search_no_context(self):
        """Should return error when context is missing."""
        response = await lgrep_search(query="test", path="/some/path", ctx=None)
        data = json.loads(response)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_lgrep_index_invalid_path(self):
        """Should return error for nonexistent directory."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_index(path="/nonexistent/path/xyz", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "does not exist" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_index_missing_api_key(self, tmp_path):
        """Should return error when VOYAGE_API_KEY is not set."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()  # No voyage_api_key
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_index(path=str(tmp_path), ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "VOYAGE_API_KEY" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_status_no_db(self):
        """Should return empty projects list when no database is initialized."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_status(ctx=mock_ctx)
        data = json.loads(response)
        assert data["projects"] == []

    @pytest.mark.asyncio
    async def test_lgrep_watch_stop_when_not_watching(self):
        """Should return graceful response when not watching."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_stop(ctx=mock_ctx)
        data = json.loads(response)
        assert data["stopped"] is True
        assert data["projects_stopped"] == []

    @pytest.mark.asyncio
    async def test_lgrep_watch_start_invalid_path(self):
        """Should return error for nonexistent path."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_start(path="/nonexistent/path", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data


class TestMaxProjectsLimit:
    """Tests for MAX_PROJECTS resource guard."""

    @pytest.mark.asyncio
    async def test_max_projects_rejects_at_limit(self, tmp_path):
        """Should reject new projects when MAX_PROJECTS limit is reached."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Pre-fill projects dict to MAX_PROJECTS
        for i in range(MAX_PROJECTS):
            app_ctx.projects[f"/fake/project/{i}"] = ProjectState(
                db=MagicMock(), indexer=MagicMock()
            )

        assert len(app_ctx.projects) == MAX_PROJECTS

        # Try to add one more
        new_path = tmp_path / "overflow"
        new_path.mkdir()
        result = await _ensure_project_initialized(app_ctx, new_path)

        # Should return error string, not ProjectState
        assert isinstance(result, str)
        data = json.loads(result)
        assert "error" in data
        assert "Maximum project limit" in data["error"]
        assert "Restart the server" in data["error"]

    @pytest.mark.asyncio
    async def test_projects_below_limit_succeed(self, tmp_path):
        """Should allow new projects below MAX_PROJECTS limit."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Pre-fill to one below limit
        for i in range(MAX_PROJECTS - 1):
            app_ctx.projects[f"/fake/project/{i}"] = ProjectState(
                db=MagicMock(), indexer=MagicMock()
            )

        new_path = tmp_path / "ok_project"
        new_path.mkdir()

        with patch("lgrep.server.VoyageEmbedder"):
            result = await _ensure_project_initialized(app_ctx, new_path)

        assert isinstance(result, ProjectState)
        assert len(app_ctx.projects) == MAX_PROJECTS


class TestWatcherBehavior:
    """Tests for watcher start/stop edge cases."""

    @pytest.mark.asyncio
    async def test_watch_start_already_watching(self, tmp_path):
        """Should return 'Already watching' when watcher is already active."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Set up a ProjectState that is already watching
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=MagicMock(),
            watching=True,
        )
        path_key = str(tmp_path.resolve())
        app_ctx.projects[path_key] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_start(path=str(tmp_path), ctx=mock_ctx)
        data = json.loads(response)

        assert data["watching"] is True
        assert data["message"] == "Already watching"

    @pytest.mark.asyncio
    async def test_stop_watcher_resets_state(self):
        """_stop_watcher should set watching=False and watcher=None."""
        mock_watcher = MagicMock()
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=mock_watcher,
            watching=True,
        )

        result = _stop_watcher(state, "/some/path")

        assert result is True
        assert state.watching is False
        assert state.watcher is None
        mock_watcher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_watcher_noop_when_not_watching(self):
        """_stop_watcher should return False when not watching."""
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=None,
            watching=False,
        )

        result = _stop_watcher(state, "/some/path")
        assert result is False


class TestRemoveTool:
    """Tests for remove_project function (CLI-only, not an MCP tool)."""

    @pytest.mark.asyncio
    async def test_remove_loaded_project(self):
        """Should remove a project from memory and stop its watcher."""
        app_ctx = LgrepContext()

        mock_watcher = MagicMock()
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=mock_watcher,
            watching=True,
        )
        app_ctx.projects["/path"] = state

        data = remove_project(app_ctx, "/path")

        assert data["removed"] is True
        assert data["remaining_projects"] == 0
        assert "/path" not in app_ctx.projects
        mock_watcher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_not_loaded_project(self):
        """Should return graceful message for unloaded project."""
        app_ctx = LgrepContext()

        data = remove_project(app_ctx, "/not/loaded")

        assert data["removed"] is False
        assert "not loaded" in data["message"].lower()


class TestLifecycle:
    """Tests for startup/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_all_watchers_and_clears(self):
        """_shutdown should stop all watchers, clear projects, and null embedder."""
        watcher_a = MagicMock()
        watcher_b = MagicMock()

        ctx = LgrepContext()
        ctx.embedder = MagicMock()
        ctx.projects["/a"] = ProjectState(
            db=MagicMock(), indexer=MagicMock(), watcher=watcher_a, watching=True
        )
        ctx.projects["/b"] = ProjectState(
            db=MagicMock(), indexer=MagicMock(), watcher=watcher_b, watching=True
        )

        await _shutdown(ctx)

        watcher_a.stop.assert_called_once()
        watcher_b.stop.assert_called_once()
        assert len(ctx.projects) == 0
        assert ctx.embedder is None
