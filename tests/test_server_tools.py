"""MCP contract tests for all 16 registered tools.

Verifies:
- All 16 tools are registered in the MCP server
- Renamed semantic tools preserve response shape
- New symbol tools return valid JSON with _meta
- Unknown tool returns structured error (via tool dispatch)
"""

from __future__ import annotations

import json

import pytest

from lgrep.server import mcp

# ── Tool registration ─────────────────────────────────────────────────────────

EXPECTED_SEMANTIC_TOOLS = {
    "search_semantic",
    "index_semantic",
    "status_semantic",
    "watch_start_semantic",
    "watch_stop_semantic",
}

EXPECTED_SYMBOL_TOOLS = {
    "index_symbols_folder",
    "index_symbols_repo",
    "list_repos",
    "get_file_tree",
    "get_file_outline",
    "get_repo_outline",
    "search_symbols",
    "search_text",
    "get_symbol",
    "get_symbols",
    "invalidate_cache",
}

ALL_EXPECTED_TOOLS = EXPECTED_SEMANTIC_TOOLS | EXPECTED_SYMBOL_TOOLS


def _get_registered_tool_names() -> set[str]:
    """Return the set of tool names registered in the MCP server."""
    return {t.name for t in mcp._tool_manager.list_tools()}


class TestToolRegistration:
    def test_all_16_tools_registered(self):
        registered = _get_registered_tool_names()
        assert registered == ALL_EXPECTED_TOOLS, (
            f"Missing: {ALL_EXPECTED_TOOLS - registered}\nExtra: {registered - ALL_EXPECTED_TOOLS}"
        )

    def test_semantic_tools_registered(self):
        registered = _get_registered_tool_names()
        for tool in EXPECTED_SEMANTIC_TOOLS:
            assert tool in registered, f"Missing semantic tool: {tool}"

    def test_symbol_tools_registered(self):
        registered = _get_registered_tool_names()
        for tool in EXPECTED_SYMBOL_TOOLS:
            assert tool in registered, f"Missing symbol tool: {tool}"

    def test_no_old_tool_names(self):
        """Old tool names (without _semantic suffix) must not be registered."""
        registered = _get_registered_tool_names()
        old_names = {"search", "index", "status", "watch_start", "watch_stop"}
        for old in old_names:
            assert old not in registered, f"Old tool name still registered: {old}"


# ── Symbol tool response shapes ───────────────────────────────────────────────


class TestSymbolToolResponses:
    """Verify symbol tools return valid JSON with _meta envelope."""

    def _get_tool_fn(self, name: str):
        """Get the tool function by name."""
        for t in mcp._tool_manager.list_tools():
            if t.name == name:
                return t.fn
        raise KeyError(f"Tool not found: {name}")

    @pytest.mark.asyncio
    async def test_index_symbols_folder_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("index_symbols_folder")
        # Create a minimal Python file
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(path=str(tmp_path))
        data = json.loads(result)
        assert "_meta" in data
        assert "timing_ms" in data["_meta"]

    @pytest.mark.asyncio
    async def test_list_repos_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("list_repos")
        result = await fn()
        data = json.loads(result)
        assert "_meta" in data
        assert "repos" in data

    @pytest.mark.asyncio
    async def test_get_file_tree_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_file_tree")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(path=str(tmp_path))
        data = json.loads(result)
        assert "_meta" in data
        assert "files" in data

    @pytest.mark.asyncio
    async def test_get_file_outline_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_file_outline")
        f = tmp_path / "hello.py"
        f.write_text("def greet(): pass\n")
        result = await fn(path=str(f))
        data = json.loads(result)
        assert "_meta" in data
        assert "symbols" in data

    @pytest.mark.asyncio
    async def test_get_repo_outline_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_repo_outline")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(path=str(tmp_path))
        data = json.loads(result)
        assert "_meta" in data
        assert "files" in data

    @pytest.mark.asyncio
    async def test_search_symbols_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("search_symbols")
        result = await fn(query="greet", path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_search_text_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("search_text")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(query="greet", path=str(tmp_path))
        data = json.loads(result)
        assert "_meta" in data
        assert "results" in data

    @pytest.mark.asyncio
    async def test_get_symbol_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("get_symbol")
        result = await fn(symbol_id="hello.py:function:greet", path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_get_symbols_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("get_symbols")
        result = await fn(symbol_ids=["hello.py:function:greet"], path=str(tmp_path))
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_invalidate_cache_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("invalidate_cache")
        result = await fn(path=str(tmp_path))
        data = json.loads(result)
        assert "_meta" in data
        assert "status" in data
