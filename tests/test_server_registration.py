"""Test that all 20 MCP tools are registered after the server split."""


def test_server_has_21_tools():
    from lgrep.server import mcp

    tool_count = len(mcp._tool_manager._tools)
    assert tool_count == 21, f"Expected 21 tools, got {tool_count}"


def test_all_expected_tools_present():
    from lgrep.server import mcp

    expected = {
        "search_semantic",
        "index_semantic",
        "status_semantic",
        "watch_start_semantic",
        "watch_stop_semantic",
        "index_symbols_folder",
        "index_symbols_repo",
        "list_repos",
        "get_file_tree",
        "get_file_outline",
        "get_repo_outline",
        "search_symbols",
        "search_text",
        "search_references",
        "get_symbol",
        "get_symbols",
        "invalidate_cache",
        "prune_orphans",
        "prune_symbols",
        "invalidate_worktree_cache",
        "lgrep_diagnostics",
    }
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    assert registered == expected, (
        f"Missing: {expected - registered}\nExtra: {registered - expected}"
    )


EXPECTED_DESTRUCTIVE_TOOLS = frozenset(
    {"prune_orphans", "prune_symbols", "invalidate_cache", "invalidate_worktree_cache"}
)


def test_destructive_tools_match_registry():
    """Registry-derived destructive population: fail visibly when the set drifts."""
    from lgrep.server import mcp

    destructive = {
        t.name
        for t in mcp._tool_manager.list_tools()
        if t.annotations is not None and t.annotations.destructiveHint is True
    }
    assert destructive == EXPECTED_DESTRUCTIVE_TOOLS, (
        f"Destructive tool set changed. Expected {EXPECTED_DESTRUCTIVE_TOOLS}, got {destructive}. "
        "If a destructive tool was added, extend grant coverage; if one was removed, update pins."
    )
