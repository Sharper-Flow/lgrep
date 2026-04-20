"""Maintenance MCP tools for the lgrep server.

Currently exposes a single tool, ``prune_orphans``, which inspects (and
optionally deletes) orphaned semantic cache directories. Kept in its
own module because it is neither a semantic-search tool nor a
symbol-intelligence tool — grouping it with either would confuse the
response contracts and the tool organisation in ``@mcp.tool`` metadata.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from mcp.server.fastmcp import Context  # noqa: TC002 — FastMCP evaluates annotations at runtime
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import mcp, time_tool
from lgrep.server.responses import (
    PruneOrphansResult,  # noqa: TC001 — FastMCP evaluates return annotation at runtime
)
from lgrep.tools.prune_orphans import prune_orphans as _prune_orphans


def _transport_is_local(ctx: Context | None) -> bool:
    """Return True when the MCP transport is the local stdio pipe.

    The stdio transport is single-user and inherently access-controlled
    (the caller is the same process tree as the server). Shared HTTP
    transports are not, so we apply a defensive ``dry_run=True`` on
    destructive tools when the transport is unknown or non-stdio.

    FastMCP does not currently expose the transport kind through the
    lifespan context, so this helper treats "unknown" the same as
    "not stdio" and errs on the side of refusing the destructive call.
    """
    if ctx is None:
        # No context means a direct test-path call (unit tests).
        # Trust the caller — tests pass explicit dry_run.
        return True
    app_ctx = getattr(ctx.request_context, "lifespan_context", None)
    if app_ctx is None:
        return False
    transport = getattr(app_ctx, "transport", None)
    if transport is None:
        transport = getattr(app_ctx, "transport_kind", None)
    if isinstance(transport, str):
        return transport.lower() in {"stdio", "local", "inline"}
    return False


@mcp.tool(
    description=(
        "Prune orphaned semantic cache directories. Dry-run by default; set "
        "dry_run=false to delete. Skips active in-memory projects and the "
        "symbols/ cache subtree. Destructive on HTTP transports is refused for "
        "safety — run the CLI (`lgrep prune-orphans --execute`) instead. "
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

    Applies a transport-aware safety: if the MCP transport is not stdio
    (i.e. a shared HTTP/SSE deployment) the handler coerces
    ``dry_run=True`` regardless of the caller's request. Operators that
    need destructive prunes on shared servers should run the CLI
    (``lgrep prune-orphans --execute``) out-of-band.
    """
    active_set: list[str] = []
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
        active_set = list(app_ctx.projects.keys())

    effective_dry_run = dry_run
    if not dry_run and not _transport_is_local(ctx):
        effective_dry_run = True

    return await asyncio.to_thread(
        _prune_orphans,
        dry_run=effective_dry_run,
        active_set=active_set,
    )
