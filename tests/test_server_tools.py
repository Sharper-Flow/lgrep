"""MCP contract tests for all 21 registered tools.

Verifies:
- All 21 tools are registered in the MCP server (5 semantic + 15 symbol/admin + 1 diagnostics)
- Renamed semantic tools preserve response shape
- New symbol/admin tools return valid JSON with _meta envelope
- Unknown tool returns structured error (via tool dispatch)
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from lgrep.server import mcp, tools_symbols

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
    "search_references",
    "get_symbol",
    "get_symbols",
    "invalidate_cache",
    "prune_orphans",
    "prune_symbols",
    "invalidate_worktree_cache",
}

EXPECTED_DIAGNOSTICS_TOOLS = {
    "lgrep_diagnostics",
}

ALL_EXPECTED_TOOLS = EXPECTED_SEMANTIC_TOOLS | EXPECTED_SYMBOL_TOOLS | EXPECTED_DIAGNOSTICS_TOOLS


def _get_registered_tool_names() -> set[str]:
    """Return the set of tool names registered in the MCP server."""
    return {t.name for t in mcp._tool_manager.list_tools()}


class TestToolRegistration:
    def test_all_21_tools_registered(self):
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
        assert "timing_ms" in data["_meta"]
        assert data["files_deleted"] == 0
        assert data["occurrences_indexed"] == 0

    @pytest.mark.asyncio
    async def test_list_repos_returns_json_with_meta(self, tmp_path, monkeypatch):
        # list_repos() backfills sidecars on read, so it is no longer
        # side-effect-free: without this patch the test would write into the
        # REAL default symbol store (~/.cache/lgrep/symbols).
        from lgrep.storage import index_store as index_store_mod

        monkeypatch.setattr(index_store_mod, "DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
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
    async def test_get_file_tree_meta_reports_handler_duration(self, tmp_path, monkeypatch):
        import time

        import lgrep.server.tools_symbols as symbols_module

        def slow_get_file_tree(path, max_files=500):
            time.sleep(0.02)
            return {"files": [], "total_files": 0}

        monkeypatch.setattr(symbols_module, "_get_file_tree", slow_get_file_tree)
        fn = self._get_tool_fn("get_file_tree")

        result = await fn(path=str(tmp_path))

        assert result["_meta"]["timing_ms"] >= 10

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
        assert data["max_results"] == 50
        assert data["error"] == ""

    @pytest.mark.asyncio
    async def test_search_text_missing_path_returns_structured_error(self, tmp_path):
        fn = self._get_tool_fn("search_text")
        result = await fn(query="greet", path=str(tmp_path / "missing"))
        assert "error" in result
        assert result["results"] == []
        assert result["max_results"] == 50

    @pytest.mark.asyncio
    async def test_search_text_uses_runtime_supervisor_when_context_available(self, tmp_path):
        fn = self._get_tool_fn("search_text")
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        calls = []

        class RuntimeStub:
            async def run_blocking(self, kind, caller, project, fn_to_run, *args, **kwargs):
                calls.append(
                    {
                        "kind": kind,
                        "caller": caller,
                        "project": project,
                    }
                )
                return fn_to_run(*args, **kwargs)

        ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context=SimpleNamespace(runtime=RuntimeStub()))
        )

        result = await fn(query="greet", path=str(tmp_path), ctx=ctx)

        assert result["results"]
        assert calls == [
            {
                "kind": "search_text",
                "caller": "search_text",
                "project": str(tmp_path.resolve()),
            }
        ]

    @pytest.mark.asyncio
    async def test_search_text_timeout_keeps_schema_shape(self, monkeypatch):
        import lgrep.server as server_mod

        monkeypatch.setattr(server_mod, "TOOL_TIMEOUT_S", 0.01)

        @server_mod.time_tool
        async def search_text(max_results=7):
            await asyncio.sleep(0.05)

        result = await search_text(max_results=7)

        assert result["results"] == []
        assert result["max_results"] == 7
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_references_missing_index_returns_error(self, tmp_path):
        fn = self._get_tool_fn("search_references")
        result = await fn(query="greet", path=str(tmp_path))
        data = result
        assert "error" in data

    @pytest.mark.asyncio
    async def test_search_references_cancellation_keeps_schema_shape(self):
        import lgrep.server as server_mod

        @server_mod.time_tool
        async def search_references(query="greet", usage_filter="production_first"):
            raise asyncio.CancelledError

        result = await search_references(query="greet", usage_filter="tests_only")

        assert result["query"] == "greet"
        assert result["usage_filter"] == "tests_only"
        assert result["total_matches"] == 0
        assert result["production_matches"] == 0
        assert result["test_matches"] == 0
        assert result["returned_production"] == 0
        assert result["returned_tests"] == 0
        assert result["stale_file_count"] == 0
        assert result["results"] == []
        assert result["candidate_names"] == []
        assert result["disclaimer"] == ""
        assert result["_meta"]["tool"] == "search_references"
        assert result["_meta"]["timing_ms"] > 0
        assert result["error"] == "Operation was cancelled."

    @pytest.mark.asyncio
    async def test_search_references_timeout_keeps_schema_shape(self, monkeypatch):
        import lgrep.server as server_mod

        monkeypatch.setattr(server_mod, "TOOL_TIMEOUT_S", 0.01)

        @server_mod.time_tool
        async def search_references(query="greet", usage_filter="production_first"):
            await asyncio.sleep(0.05)

        result = await search_references(query="greet", usage_filter="tests_only")

        assert result["query"] == "greet"
        assert result["usage_filter"] == "tests_only"
        assert result["total_matches"] == 0
        assert result["production_matches"] == 0
        assert result["test_matches"] == 0
        assert result["returned_production"] == 0
        assert result["returned_tests"] == 0
        assert result["stale_file_count"] == 0
        assert result["results"] == []
        assert result["candidate_names"] == []
        assert result["disclaimer"] == ""
        assert result["_meta"]["tool"] == "search_references"
        assert result["error"].startswith("Operation timed out after")

    @pytest.mark.asyncio
    async def test_search_references_uses_runtime_supervisor_when_context_available(self, tmp_path):
        fn = self._get_tool_fn("search_references")
        calls = []

        class RuntimeStub:
            async def run_blocking(self, kind, caller, project, fn_to_run, *args, **kwargs):
                calls.append({"kind": kind, "caller": caller, "project": project})
                return fn_to_run(*args, **kwargs)

        ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context=SimpleNamespace(runtime=RuntimeStub()))
        )

        result = await fn(query="greet", path=str(tmp_path), ctx=ctx)

        assert "error" in result  # repo not indexed is fine for this contract test
        assert calls == [
            {
                "kind": "search_references",
                "caller": "search_references",
                "project": str(tmp_path.resolve()),
            }
        ]

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


class TestPruneSymbolsTool:
    """MCP contract and transport-safety tests for prune_symbols."""

    def _get_tool_fn(self, name: str):
        """Get the tool function by name."""
        for t in mcp._tool_manager.list_tools():
            if t.name == name:
                return t.fn
        raise KeyError(f"Tool not found: {name}")

    def _make_context(self, transport: str):
        """Build a fake MCP Context with the given transport kind."""

        class RuntimeStub:
            async def run_blocking(self, kind, caller, project, fn_to_run, *args, **kwargs):
                return fn_to_run(*args, **kwargs)

        return SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context=SimpleNamespace(
                    transport=transport,
                    projects={},
                    runtime=RuntimeStub(),
                )
            )
        )

    @pytest.mark.asyncio
    async def test_prune_symbols_registered_as_mcp_tool(self):
        fn = self._get_tool_fn("prune_symbols")
        assert fn is not None

    @pytest.mark.asyncio
    async def test_mcp_prune_symbols_dry_run_default_response_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path))
        fn = self._get_tool_fn("prune_symbols")
        result = await fn()
        assert isinstance(result, dict)
        assert {
            "dry_run",
            "files_examined",
            "stale_indexes",
            "skipped_active",
            "deleted_files",
            "reclaimed_bytes",
            "failures",
            "_meta",
        } <= set(result.keys())
        assert result["dry_run"] is True

    @pytest.mark.asyncio
    async def test_mcp_prune_symbols_stdio_alone_does_not_authorize(self, tmp_path, monkeypatch):
        """Stdio is not evidence of a single local caller once a proxy fronts it.

        Vision runs lgrep as a stdio subprocess behind a shared, unauthenticated
        HTTP port, so the destructive run needs the explicit server-side grant.
        """
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path))
        monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)
        fn = self._get_tool_fn("prune_symbols")
        ctx = self._make_context("stdio")
        result = await fn(dry_run=False, ctx=ctx)
        assert result["dry_run"] is True
        assert "LGREP_ALLOW_DESTRUCTIVE_MCP" in result["refused_reason"]

    @pytest.mark.asyncio
    async def test_mcp_prune_symbols_honors_dry_run_false_with_grant(self, tmp_path, monkeypatch):
        """With the explicit grant, the caller's destructive request is honoured."""
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path))
        monkeypatch.setenv("LGREP_ALLOW_DESTRUCTIVE_MCP", "1")
        fn = self._get_tool_fn("prune_symbols")
        ctx = self._make_context("stdio")
        result = await fn(dry_run=False, ctx=ctx)
        assert result["dry_run"] is False

    @pytest.mark.asyncio
    async def test_mcp_prune_symbols_without_grant_coerces_dry_run_true(
        self, tmp_path, monkeypatch
    ):
        """Coercion is grant-based, not transport-based.

        The streamable-http transport here is incidental: the same request would
        also be refused on stdio, and would be honoured on either transport once
        the grant is set.
        """
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path))
        monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)
        fn = self._get_tool_fn("prune_symbols")
        ctx = self._make_context("streamable-http")
        result = await fn(dry_run=False, ctx=ctx)
        assert result["dry_run"] is True


# ── Wrapper bad-input contract ────────────────────────────────────────────────

MISSING_DIR = "/nonexistent/lgrep-wrapper-contract-dir"
MISSING_FILE = "/nonexistent/lgrep-wrapper-contract-dir/absent.py"
LOCAL_PATH_FOR_REPO_TOOL = "/nonexistent/lgrep-wrapper-contract-local/repo"


class TestWrapperBadInputPerTool:
    """A bad input returns a ToolError dict from every repaired wrapper.

    Covers check:pytest/wrapper-bad-input-per-tool: each wrapper that reads
    helper success keys returns the helper's error instead of raising KeyError
    when the helper reports an error.
    """

    def _get_tool_fn(self, name: str):
        """Get the tool function by name."""
        for t in mcp._tool_manager.list_tools():
            if t.name == name:
                return t.fn
        raise KeyError(f"Tool not found: {name}")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "arguments"),
        [
            ("index_symbols_folder", {"path": MISSING_DIR}),
            ("index_symbols_repo", {"repo": LOCAL_PATH_FOR_REPO_TOOL}),
            ("get_file_tree", {"path": MISSING_DIR}),
            ("get_file_outline", {"path": MISSING_FILE}),
            ("get_repo_outline", {"path": MISSING_DIR}),
        ],
    )
    async def test_bad_input_returns_tool_error_not_keyerror(self, tool_name, arguments):
        fn = self._get_tool_fn(tool_name)
        result = await fn(**arguments)
        assert isinstance(result, dict), (
            f"{tool_name} returned {type(result).__name__}, expected a ToolError dict"
        )
        assert set(result) == {"error"}, f"{tool_name} returned non-error dict: {result}"
        assert isinstance(result["error"], str)
        assert result["error"], f"{tool_name} returned an empty error message"

    @pytest.mark.asyncio
    async def test_get_file_outline_directory_is_not_a_file(self, tmp_path):
        """A directory reports 'not a file'; a missing path reports 'does not exist'."""
        fn = self._get_tool_fn("get_file_outline")
        directory_result = await fn(path=str(tmp_path))
        missing_result = await fn(path=str(tmp_path / "absent.py"))
        assert "error" in directory_result
        assert "not a file" in directory_result["error"]
        assert "error" in missing_result
        assert "does not exist" in missing_result["error"]
        assert directory_result["error"] != missing_result["error"]

    @pytest.mark.asyncio
    async def test_index_symbols_repo_local_path_names_folder_tool(self):
        """An owner/name format error for a local path points at index_symbols_folder."""
        fn = self._get_tool_fn("index_symbols_repo")
        local_path_result = await fn(repo=LOCAL_PATH_FOR_REPO_TOOL)
        assert "error" in local_path_result
        assert "index_symbols_folder" in local_path_result["error"]
        malformed_result = await fn(repo="notowner/name/extra")
        assert "error" in malformed_result
        assert "index_symbols_folder" not in malformed_result["error"]


class TestEverySymbolToolPassesHelperError:
    """Every registered symbol tool returns its helper's error message.

    Covers check:pytest/every-symbol-tool-passes-helper-error. The tool list
    comes from the MCP registry, and every lgrep.tools helper the module calls
    is stubbed to report an error, so a symbol tool added later is covered
    without editing this test.
    """

    STUB_ERROR = "stub helper error"

    # Helpers of these tools have no error branch, so there is no error to pass on.
    NO_HELPER_ERROR = {"list_repos", "invalidate_cache"}

    @staticmethod
    def _symbol_tools():
        return sorted(
            (
                t
                for t in mcp._tool_manager.list_tools()
                if t.fn.__module__ == tools_symbols.__name__
            ),
            key=lambda t: t.name,
        )

    @classmethod
    def _stub_helpers(cls, monkeypatch):
        def sync_stub(*_args, **_kwargs):
            return {"error": cls.STUB_ERROR}

        async def async_stub(*_args, **_kwargs):
            return {"error": cls.STUB_ERROR}

        stubbed = 0
        for name, value in vars(tools_symbols).items():
            if (
                name.startswith("_")
                and inspect.isfunction(value)
                and value.__module__.startswith("lgrep.tools.")
            ):
                stub = async_stub if inspect.iscoroutinefunction(value) else sync_stub
                monkeypatch.setattr(tools_symbols, name, stub)
                stubbed += 1
        assert stubbed, "no lgrep.tools helpers found in tools_symbols"

    @staticmethod
    def _arguments(tool) -> dict:
        arguments = {}
        for name, prop in tool.parameters.get("properties", {}).items():
            if name not in tool.parameters.get("required", []):
                continue
            if prop.get("type") == "array":
                arguments[name] = ["src/absent.py:function:absent"]
            elif name == "repo":
                arguments[name] = "owner/name"
            else:
                arguments[name] = "/nonexistent/lgrep-guard"
        return arguments

    def test_registry_finds_the_symbol_tools(self):
        names = {t.name for t in self._symbol_tools()}
        assert names <= EXPECTED_SYMBOL_TOOLS
        assert {"get_file_tree", "get_file_outline", "search_symbols"} <= names

    @pytest.mark.asyncio
    async def test_every_symbol_tool_returns_helper_error(self, monkeypatch):
        self._stub_helpers(monkeypatch)
        failures = []
        for tool in self._symbol_tools():
            if tool.name in self.NO_HELPER_ERROR:
                continue
            try:
                result = await tool.fn(**self._arguments(tool))
            except Exception as exc:  # noqa: BLE001 - each failure is reported below
                failures.append(f"{tool.name}: raised {type(exc).__name__}: {exc}")
                continue
            if not isinstance(result, dict) or result.get("error") != self.STUB_ERROR:
                failures.append(f"{tool.name}: returned {result!r}")
        assert not failures, "\n".join(failures)
