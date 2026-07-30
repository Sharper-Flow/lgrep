"""Destructive MCP maintenance requires an explicit capability grant.

Regression cover for rq-destructiveGrant01. The previous guard trusted a
transport reported as ``stdio``, on the assumption that stdio implies a
single local caller. Vision breaks that assumption: it runs lgrep as a stdio
subprocess and republishes it on a shared, unauthenticated HTTP port, so a
proxied client inherited full destructive rights.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lgrep.server import mcp

GRANT_ENV = "LGREP_ALLOW_DESTRUCTIVE_MCP"
DESTRUCTIVE_TOOLS = ("prune_orphans", "prune_symbols")


def _tool_fn(name: str):
    for tool in mcp._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    raise KeyError(f"Tool not found: {name}")


class _InlineRuntime:
    """Runs supervised work inline so the guard, not the scheduler, is under test."""

    async def run_blocking(self, kind, caller, project, fn, *args, **kwargs):
        kwargs.pop("cancel_event", None)
        return fn(*args, **kwargs)


def _stdio_ctx():
    """A context that reports the transport as local stdio.

    This is exactly what lgrep reports when Vision proxies it, which is why
    transport must not decide authority.
    """
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                projects={}, runtime=_InlineRuntime(), transport="stdio"
            )
        )
    )


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_destructive_refused_without_grant_even_on_stdio(tool_name, monkeypatch):
    monkeypatch.delenv(GRANT_ENV, raising=False)

    result = await _tool_fn(tool_name)(dry_run=False, ctx=_stdio_ctx())

    assert result["dry_run"] is True, (
        f"{tool_name} performed a destructive run without the {GRANT_ENV} grant. "
        "A stdio transport behind a shared proxy must not confer destructive rights."
    )
    assert result["deleted_dirs" if tool_name == "prune_orphans" else "deleted_files"] == 0

    reason = result.get("refused_reason")
    assert reason, f"{tool_name} must report why the destructive run was refused"
    assert GRANT_ENV in reason, "refusal must name the required grant"
    assert tool_name.replace("_", "-") in reason, "refusal must name the CLI equivalent"


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_destructive_allowed_with_grant(tool_name, monkeypatch):
    monkeypatch.setenv(GRANT_ENV, "1")

    result = await _tool_fn(tool_name)(dry_run=False, ctx=_stdio_ctx())

    assert result["dry_run"] is False, (
        f"{tool_name} must honour the caller's destructive request once {GRANT_ENV} is set"
    )
    assert result.get("refused_reason") is None


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_preview_request_is_never_marked_refused(tool_name, monkeypatch):
    """A caller that asked for a preview was not refused anything."""
    monkeypatch.delenv(GRANT_ENV, raising=False)

    result = await _tool_fn(tool_name)(dry_run=True, ctx=_stdio_ctx())

    assert result["dry_run"] is True
    assert result.get("refused_reason") is None


def test_transport_no_longer_decides_authority():
    """The transport-trust helper must be gone, not merely bypassed."""
    from lgrep.server import tools_maintenance

    assert not hasattr(tools_maintenance, "_transport_is_local"), (
        "transport-based trust inference must be removed; a proxy can front a "
        "local stdio pipe with a shared network surface"
    )
