"""Maintenance MCP tools for the lgrep server.

Currently exposes ``prune_orphans``, ``prune_symbols`` and
``invalidate_worktree_cache``. Kept in its own module because these are
neither semantic-search tools nor symbol-intelligence tools — grouping
them with either would confuse the response contracts and the tool
organisation in ``@mcp.tool`` metadata.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Annotated

from mcp.server.fastmcp import Context  # noqa: TC002 — FastMCP evaluates annotations at runtime
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import mcp, time_tool
from lgrep.server.responses import (
    PruneOrphansResult,  # noqa: TC001 — FastMCP evaluates return annotation at runtime
    PruneSymbolsResult,  # noqa: TC001
    WorktreeInvalidationResult,  # noqa: TC001
)
from lgrep.tools._meta import make_meta
from lgrep.tools.invalidate_worktree import (
    invalidate_worktree_cache as _invalidate_worktree_cache,
)
from lgrep.tools.prune_orphans import prune_orphans as _prune_orphans
from lgrep.tools.prune_symbols import prune_symbols as _prune_symbols


async def _run_blocking(
    ctx: Context | None,
    kind: str,
    project: str | None,
    fn,
    *args,
    **kwargs,
):
    app_ctx = None
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
    if app_ctx is not None:
        return await app_ctx.runtime.run_blocking(
            kind,
            "tools_maintenance",
            project,
            fn,
            *args,
            **kwargs,
        )
    return await asyncio.to_thread(fn, *args, **kwargs)


_DESTRUCTIVE_GRANT_ENV = "LGREP_ALLOW_DESTRUCTIVE_MCP"


def _destructive_grant_present() -> bool:
    """Return True when the operator has explicitly granted destructive MCP rights.

    Authority is an explicit, out-of-band environment grant rather than an
    inference from the transport. A transport reported as ``stdio`` says
    nothing about the caller once a proxy fronts the pipe: Vision runs lgrep
    as a stdio subprocess and republishes it on a shared, unauthenticated
    HTTP port, so every proxied client would otherwise inherit destructive
    rights. Defaulting to off keeps the shared deployment fail-closed.

    The CLI does not consult this grant; that caller already holds local
    shell authority.
    """
    return os.environ.get(_DESTRUCTIVE_GRANT_ENV, "").lower() in ("true", "1", "yes")


def _refusal_reason(cli_command: str) -> str:
    return (
        f"Destructive run refused: {_DESTRUCTIVE_GRANT_ENV} is not set. "
        f"Returned a preview instead. Set {_DESTRUCTIVE_GRANT_ENV}=1 on the server "
        f"to allow destructive MCP calls, or run `{cli_command}` locally."
    )


@mcp.tool(
    description=(
        "Prune orphaned semantic cache directories. Dry-run by default; set "
        "dry_run=false to delete. Skips active in-memory projects and the "
        "symbols/ cache subtree. Deletion over MCP requires the server to set "
        "LGREP_ALLOW_DESTRUCTIVE_MCP=1; otherwise the call returns a preview and "
        "says so — run the CLI (`lgrep prune-orphans --execute`) instead. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@time_tool
async def prune_orphans(
    dry_run: Annotated[
        bool,
        Field(description="Preview only when true; actually delete orphan caches when false."),
    ] = True,
    ctx: Context | None = None,
) -> PruneOrphansResult:
    """Inspect or delete orphan semantic cache directories.

    Destructive runs require the ``LGREP_ALLOW_DESTRUCTIVE_MCP`` grant on the
    server. Without it the handler coerces ``dry_run=True`` and reports why.
    Operators can always run the CLI (``lgrep prune-orphans --execute``).
    """
    active_set: list[str] = []
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
        active_set = list(app_ctx.projects.keys())

    effective_dry_run = dry_run
    refused_reason: str | None = None
    if not dry_run and not _destructive_grant_present():
        effective_dry_run = True
        refused_reason = _refusal_reason("lgrep prune-orphans --execute")

    result = await _run_blocking(
        ctx,
        "prune_orphans",
        None,
        _prune_orphans,
        dry_run=effective_dry_run,
        active_set=active_set,
    )
    if refused_reason is not None:
        result["refused_reason"] = refused_reason
    return result


@mcp.tool(
    description=(
        "Prune stale symbol-store index files. Dry-run by default; set "
        "dry_run=false to delete. Skips active in-memory projects. "
        "Deletion over MCP requires the server to set LGREP_ALLOW_DESTRUCTIVE_MCP=1; "
        "otherwise the call returns a preview and says so — run the "
        "CLI (`lgrep prune-symbols --execute`) instead. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@time_tool
async def prune_symbols(
    dry_run: Annotated[
        bool,
        Field(description="Preview only when true; actually delete stale indexes when false."),
    ] = True,
    ctx: Context | None = None,
) -> PruneSymbolsResult:
    """Inspect or delete stale symbol-store index files.

    Destructive runs require the ``LGREP_ALLOW_DESTRUCTIVE_MCP`` grant on the
    server. Without it the handler coerces ``dry_run=True`` and reports why.
    Operators can always run the CLI (``lgrep prune-symbols --execute``).
    """
    active_set: list[str] = []
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
        active_set = list(app_ctx.projects.keys())

    effective_dry_run = dry_run
    refused_reason: str | None = None
    if not dry_run and not _destructive_grant_present():
        effective_dry_run = True
        refused_reason = _refusal_reason("lgrep prune-symbols --execute")

    result = await _run_blocking(
        ctx,
        "prune_symbols",
        None,
        _prune_symbols,
        dry_run=effective_dry_run,
        active_set=active_set,
    )
    if refused_reason is not None:
        result["refused_reason"] = refused_reason
    return result


@mcp.tool(
    description=(
        "Invalidate worktree-specific cache entries. For each path: removes the "
        "worktree alias from project_meta.json and unloads from server memory. "
        "If the canonical project path is gone and no aliases remain, the cache "
        "directory is deleted. Deletion over MCP requires the server to set "
        "LGREP_ALLOW_DESTRUCTIVE_MCP=1; otherwise the call refuses deletion and says "
        "so. There is no CLI equivalent. MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@time_tool
async def invalidate_worktree_cache(
    paths: Annotated[
        list[str],
        Field(description="Worktree paths to invalidate"),
    ],
    ctx: Context | None = None,
) -> WorktreeInvalidationResult:
    """Invalidate worktree-specific cache entries.

    For each path: computes its cache dir, removes the path from
    ``project_meta.json`` alias list, and if the canonical project is gone
    and no aliases remain, deletes the cache dir. Invalidated paths are
    also removed from the server's in-memory project state.

    Destructive runs require the ``LGREP_ALLOW_DESTRUCTIVE_MCP`` grant on the
    server. Without it the handler refuses the deletion and reports why.
    """
    t0 = time.monotonic()

    if not _destructive_grant_present():
        return WorktreeInvalidationResult(
            paths_cleaned=0,
            bytes_reclaimed=0,
            entries=[],
            refused_reason=(
                "Destructive run refused: LGREP_ALLOW_DESTRUCTIVE_MCP is not set. "
                "Worktree cache deletion is not available over MCP. "
                "Set LGREP_ALLOW_DESTRUCTIVE_MCP=1 on the server to allow destructive "
                "MCP calls. There is no CLI equivalent."
            ),
            _meta=make_meta(t0),
        )

    # Run the core invalidation logic in a thread (sync I/O)
    entries, paths_cleaned, bytes_reclaimed = await _run_blocking(
        ctx,
        "invalidate_worktree_cache",
        None,
        _invalidate_worktree_cache,
        paths=paths,
    )

    # Remove invalidated paths from in-memory server state
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
        from pathlib import Path

        for entry in entries:
            if entry.get("error") is None and entry.get("cache_dir"):
                resolved = str(Path(entry["path"]).resolve())
                if resolved in app_ctx.projects:
                    del app_ctx.projects[resolved]

    return WorktreeInvalidationResult(
        paths_cleaned=paths_cleaned,
        bytes_reclaimed=bytes_reclaimed,
        entries=entries,
        _meta=make_meta(t0),
    )
