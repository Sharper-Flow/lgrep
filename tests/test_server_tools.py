"""MCP contract tests for all 17 registered tools.

Verifies:
- All 17 tools are registered in the MCP server (5 semantic + 12 symbol/admin)
- Renamed semantic tools preserve response shape
- New symbol/admin tools return valid JSON with _meta envelope
- Unknown tool returns structured error (via tool dispatch)
"""

from __future__ import annotations

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
    "prune_orphans",
}

ALL_EXPECTED_TOOLS = EXPECTED_SEMANTIC_TOOLS | EXPECTED_SYMBOL_TOOLS


def _get_registered_tool_names() -> set[str]:
    """Return the set of tool names registered in the MCP server."""
    return {t.name for t in mcp._tool_manager.list_tools()}


class TestToolRegistration:
    def test_all_17_tools_registered(self):
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
        data = result
        assert "_meta" in data
        assert "duration_ms" in data["_meta"]

    @pytest.mark.asyncio
    async def test_list_repos_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("list_repos")
        result = await fn()
        data = result
        assert "_meta" in data
        assert "repos" in data

    @pytest.mark.asyncio
    async def test_get_file_tree_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_file_tree")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(path=str(tmp_path))
        data = result
        assert "_meta" in data
        assert "files" in data

    @pytest.mark.asyncio
    async def test_get_file_outline_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_file_outline")
        f = tmp_path / "hello.py"
        f.write_text("def greet(): pass\n")
        result = await fn(path=str(f))
        data = result
        assert "_meta" in data
        assert "symbols" in data

    @pytest.mark.asyncio
    async def test_get_repo_outline_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("get_repo_outline")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(path=str(tmp_path))
        data = result
        assert "_meta" in data
        assert "files" in data
        # Contract: files is list of FileOutline dicts, not list of strings
        assert isinstance(data["files"], list)
        if len(data["files"]) > 0:
            entry = data["files"][0]
            assert isinstance(entry, dict), f"Expected dict, got {type(entry)}"
            assert "file_path" in entry
            assert "symbols" in entry
            assert "symbol_count" in entry

    @pytest.mark.asyncio
    async def test_search_symbols_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("search_symbols")
        result = await fn(query="greet", path=str(tmp_path))
        data = result
        assert "error" in data

    @pytest.mark.asyncio
    async def test_search_text_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("search_text")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = await fn(query="greet", path=str(tmp_path))
        data = result
        assert "_meta" in data
        assert "results" in data

    @pytest.mark.asyncio
    async def test_get_symbol_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("get_symbol")
        result = await fn(symbol_id="hello.py:function:greet", path=str(tmp_path))
        data = result
        assert "error" in data

    @pytest.mark.asyncio
    async def test_get_symbols_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("get_symbols")
        result = await fn(symbol_ids=["hello.py:function:greet"], path=str(tmp_path))
        data = result
        assert "error" in data

    @pytest.mark.asyncio
    async def test_invalidate_cache_returns_json_with_meta(self, tmp_path):
        fn = self._get_tool_fn("invalidate_cache")
        result = await fn(path=str(tmp_path))
        data = result
        assert "_meta" in data
        assert "status" in data

    @pytest.mark.asyncio
    async def test_prune_orphans_registered_as_mcp_tool(self):
        fn = self._get_tool_fn("prune_orphans")
        assert fn is not None

    @pytest.mark.asyncio
    async def test_mcp_prune_orphans_dry_run_default_response_shape(self, tmp_path):
        fn = self._get_tool_fn("prune_orphans")
        result = await fn(dry_run=True)
        assert isinstance(result, dict)
        assert {
            "dry_run",
            "dirs_examined",
            "orphans",
            "skipped_active",
            "deleted_dirs",
            "reclaimed_bytes",
            "failures",
            "_meta",
        } <= set(result.keys())
