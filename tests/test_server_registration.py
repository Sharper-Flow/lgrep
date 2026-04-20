"""Test that all 16 MCP tools are registered after the server split."""



def test_server_has_16_tools():
    from lgrep.server import mcp

    tool_count = len(mcp._tool_manager._tools)
    assert tool_count == 16, f"Expected 16 tools, got {tool_count}"


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
        "get_symbol",
        "get_symbols",
        "invalidate_cache",
    }
    registered = {t.name for t in mcp._tool_manager.list_tools()}
    assert registered == expected, (
        f"Missing: {expected - registered}\nExtra: {registered - expected}"
    )
