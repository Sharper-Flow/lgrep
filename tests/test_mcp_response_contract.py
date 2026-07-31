"""FastMCP structured-output contract validation.

Regression cover for AC1/AC2: maintenance MCP tools must produce responses that
validate against their declared response schemas when FastMCP's structured-output
path is exercised. Every registered tool is also smoke-checked so the suite fails
if any tool returns data that violates its schema.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lgrep.server import mcp


def _tool(name: str):
    for t in mcp._tool_manager.list_tools():
        if t.name == name:
            return t
    raise KeyError(name)


def _runtime_ctx():
    """Return a minimal MCP context with an inline runtime supervisor."""
    import time

    class RuntimeStub:
        started_at = time.time()
        max_workers = 4

        async def run_blocking(self, kind, caller, project, fn, *args, **kwargs):
            kwargs.pop("cancel_event", None)
            return fn(*args, **kwargs)

        def snapshot_active_jobs(self):
            return []

        def snapshot_recent_jobs(self):
            return []

    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                projects={},
                runtime=RuntimeStub(),
                transport="stdio",
            )
        )
    )


async def _run_validated(tool, arguments: dict, grant: bool = False):
    """Run a tool through FastMCP's structured-output path and return the raw dict.

    First validates with ``convert_result=True`` so FastMCP checks the result
    against its declared output schema. Then returns the raw dict produced by
    the handler so callers can assert on the canonical _meta envelope, which
    FastMCP's derived schema does not surface because the field name starts
    with an underscore.
    """
    ctx = _runtime_ctx()
    await tool.run(arguments, context=ctx, convert_result=True)
    raw = await tool.run(arguments, context=ctx, convert_result=False)
    assert isinstance(raw, dict), f"expected dict, got {type(raw).__name__}"
    return raw


@pytest.mark.parametrize(
    "tool_name,arguments,needs_grant",
    [
        ("prune_orphans", {"dry_run": True}, False),
        ("prune_symbols", {"dry_run": True}, False),
        ("invalidate_worktree_cache", {"paths": []}, True),
    ],
)
@pytest.mark.asyncio
async def test_maintenance_tool_validates_on_default_path(
    tool_name: str,
    arguments: dict,
    needs_grant: bool,
    monkeypatch,
):
    """Default (non-destructive) maintenance calls must pass FastMCP validation."""
    if needs_grant:
        monkeypatch.setenv("LGREP_ALLOW_DESTRUCTIVE_MCP", "1")
    else:
        monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)

    raw = await _run_validated(_tool(tool_name), arguments)
    assert "_meta" in raw, f"{tool_name} response missing _meta envelope"
    assert raw["_meta"]["tool"] == tool_name
    # refused_reason is a non-nullable string; empty when the run was not refused
    assert "refused_reason" in raw
    assert raw.get("refused_reason") == ""


@pytest.mark.parametrize(
    "tool_name",
    [
        "prune_orphans",
        "prune_symbols",
        "invalidate_worktree_cache",
        "invalidate_cache",
    ],
)
def test_maintenance_output_schema_rejects_nullable_refused_reason(tool_name: str):
    """Vision strict response schemas reject nullable output fields (hotfix)."""
    tool = _tool(tool_name)
    refused_schema = tool.output_schema["properties"]["refused_reason"]
    assert refused_schema == {"title": "Refused Reason", "type": "string"}, (
        f"{tool_name}.refused_reason must be a non-nullable string, got {refused_schema}"
    )


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("prune_orphans", {"dry_run": False}),
        ("prune_symbols", {"dry_run": False}),
        ("invalidate_worktree_cache", {"paths": []}),
    ],
)
@pytest.mark.asyncio
async def test_maintenance_tool_refusal_path_validates_and_names_grant(
    tool_name: str,
    arguments: dict,
    monkeypatch,
):
    """Without the destructive grant, destructive calls downgrade and still validate."""
    monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)

    raw = await _run_validated(_tool(tool_name), arguments)
    assert "refused_reason" in raw
    reason = raw["refused_reason"]
    assert reason is not None
    assert "LGREP_ALLOW_DESTRUCTIVE_MCP" in reason


@pytest.mark.asyncio
async def test_all_registered_tools_validate_on_trivial_invocation(tmp_path, monkeypatch):
    """Smoke-check: every tool can be invoked through the structured-output path.

    Tools that require a real project path or indexed state may return errors, but
    those errors must still conform to the declared schema.
    """
    # Ensure destructive tools receive a consistent grant state.
    monkeypatch.delenv("LGREP_ALLOW_DESTRUCTIVE_MCP", raising=False)

    # Create a minimal valid repo for path-taking tools.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text("def greet(): pass\n")

    failures = []
    for tool in mcp._tool_manager.list_tools():
        # Build the simplest argument set that will not crash before validation.
        args = {}
        if tool.name in {"prune_orphans", "prune_symbols"}:
            args = {"dry_run": True}
        elif tool.name == "invalidate_worktree_cache":
            args = {"paths": []}
        elif tool.name == "invalidate_cache":
            # Any path is fine; without the grant it refuses and validates.
            args = {"path": str(repo)}
        elif tool.name in {"get_file_tree", "get_repo_outline", "index_symbols_folder"}:
            args = {"path": str(repo)}
        elif tool.name == "get_file_outline":
            args = {"path": str(repo / "hello.py")}
        elif tool.name in {"search_symbols", "search_text", "search_references"}:
            args = {"query": "greet", "path": str(repo)}
        elif tool.name == "get_symbol":
            args = {"symbol_id": "hello.py:function:greet", "path": str(repo)}
        elif tool.name == "get_symbols":
            args = {"symbol_ids": ["hello.py:function:greet"], "path": str(repo)}
        elif tool.name in {"index_semantic", "watch_start_semantic"}:
            # Semantic indexing requires an embedding backend; skip in this smoke test.
            continue
        elif tool.name == "index_symbols_repo":
            # Remote indexing requires network; skip in this smoke test.
            continue
        elif tool.name in {"status_semantic", "watch_stop_semantic"}:
            args = {}

        try:
            await tool.run(args, context=_runtime_ctx(), convert_result=True)
        except Exception as exc:  # noqa: BLE001
            failures.append((tool.name, f"{type(exc).__name__}: {exc}"))

    assert not failures, "schema validation failures: " + "; ".join(
        f"{name}: {msg}" for name, msg in failures
    )
