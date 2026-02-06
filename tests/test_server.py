"""Tests for server tool responses."""

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import (
    lgrep_index,
    lgrep_search,
    lgrep_status,
    lgrep_watch_start,
    lgrep_watch_stop,
    LgrepContext,
)
from lgrep.storage import SearchResult, SearchResults


class TestServerTools:
    """Tests for MCP tools in server.py."""

    @pytest.mark.asyncio
    async def test_lgrep_search_format(self):
        """Should format search results as JSON."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        app_ctx.db = MagicMock()
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        # Mock storage return
        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=10.0,
            total_chunks=100,
        )
        app_ctx.db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        response = await lgrep_search(query="test", ctx=mock_ctx)
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
        app_ctx.db = MagicMock()
        app_ctx.project_path = "/path"
        app_ctx.watching = True
        mock_ctx.request_context.lifespan_context = app_ctx

        app_ctx.db.count_chunks.return_value = 500
        app_ctx.db.get_indexed_files.return_value = {"a.py", "b.py"}

        response = await lgrep_status(ctx=mock_ctx)
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
        app_ctx = LgrepContext()  # No db or embedder set
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_search(query="test", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "lgrep_index" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_search_no_context(self):
        """Should return error when context is missing."""
        response = await lgrep_search(query="test", ctx=None)
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
        """Should return zeros when no database is initialized."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_status(ctx=mock_ctx)
        data = json.loads(response)
        assert data["files"] == 0
        assert data["chunks"] == 0
        assert data["watching"] is False

    @pytest.mark.asyncio
    async def test_lgrep_watch_stop_when_not_watching(self):
        """Should return graceful message when not watching."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_stop(ctx=mock_ctx)
        data = json.loads(response)
        assert data["stopped"] is True
        assert "Not watching" in data["message"]

    @pytest.mark.asyncio
    async def test_lgrep_watch_start_invalid_path(self):
        """Should return error for nonexistent path."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_start(path="/nonexistent/path", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
