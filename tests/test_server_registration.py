"""Pin the complete MCP registry and the per-transport tool profile."""

import pytest

from lgrep.server.bootstrap import GRANT_ONLY_TOOLS, STDIO_ONLY_TOOLS, apply_tool_profile

ALL_TOOLS = frozenset(
    {
        "search_semantic",
        "index_semantic",
        "status_semantic",
        "index_symbols_folder",
        "index_symbols_repo",
        "get_symbol_index_job",
        "list_repos",
        "get_file_tree",
        "get_file_outline",
        "get_repo_outline",
        "search_symbols",
        "search_text",
        "search_references",
        "get_symbols",
        "invalidate_cache",
        "prune_orphans",
        "prune_symbols",
        "invalidate_worktree_cache",
        "lgrep_diagnostics",
    }
)

RETIRED_TOOLS = ("get_symbol", "watch_start_semantic", "watch_stop_semantic")


def _registered() -> set[str]:
    from lgrep.server import mcp

    return {t.name for t in mcp._tool_manager.list_tools()}


def test_full_registry_before_profile():
    registered = _registered()
    assert registered == ALL_TOOLS, (
        f"Missing: {ALL_TOOLS - registered}\nExtra: {registered - ALL_TOOLS}"
    )


@pytest.mark.parametrize("name", RETIRED_TOOLS)
def test_retired_tools_are_not_registered(name):
    assert name not in _registered()


@pytest.mark.parametrize(
    ("transport", "grant", "expected_count", "hidden"),
    [
        ("stdio", None, 17, set(GRANT_ONLY_TOOLS)),
        ("streamable-http", None, 14, set(GRANT_ONLY_TOOLS) | set(STDIO_ONLY_TOOLS)),
        ("stdio", "1", 19, set()),
        ("streamable-http", "1", 16, set(STDIO_ONLY_TOOLS)),
    ],
)
def test_tool_profile(monkeypatch, transport, grant, expected_count, hidden):
    """A listed tool can succeed: grant-only and stdio-only tools hide otherwise."""
    from lgrep.server import mcp

    if grant is None:
        monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)
    else:
        monkeypatch.setenv("LGREP_ALLOW_DESTRUCTIVE_MCP", grant)

    removed = apply_tool_profile(mcp, transport)

    registered = _registered()
    assert set(removed) == hidden
    assert registered == ALL_TOOLS - hidden
    assert len(registered) == expected_count


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
