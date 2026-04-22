"""Semantic tools: search, index, status, watch for the lgrep MCP server."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import Context  # noqa: TC002
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import log, mcp, time_tool
from lgrep.server.lifecycle import (
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
    _ensure_search_project_state,
    _get_project_stats,
    _stop_watcher,
)
from lgrep.server.responses import (
    IndexSemanticResult,
    SearchSemanticResult,
    StatusAllProjectsResult,
    StatusSemanticResult,
    ToolError,
    WatchStartResult,
    WatchStopAllResult,
    WatchStopResult,
    error_response,
)
from lgrep.storage import ChunkStore, get_project_db_path, has_disk_cache
from lgrep.watcher import FileWatcher

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _execute_search(
    app_ctx: LgrepContext,
    state: ProjectState,
    query: str,
    limit: int,
    hybrid: bool,
    project_path: str,
) -> SearchSemanticResult | ToolError:
    """Run embedding + storage search and return structured result."""
    if app_ctx.embedder is None:
        return error_response("VOYAGE_API_KEY not set. Cannot perform semantic search.")
    try:
        query_vector = await app_ctx.embedder.embed_query_async(query)
        if hybrid:
            results = await asyncio.to_thread(state.db.search_hybrid, query_vector, query, limit)
        else:
            results = await asyncio.to_thread(state.db.search_vector, query_vector, limit)
        # Explicit key mapping: construct SearchChunk dicts with line_number
        # mapped from SearchResult.start_line, preserving fidelity fields.
        chunks = [
            {
                "file_path": r.file_path,
                "line_number": r.start_line,
                "content": r.content,
                "score": r.score,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "match_type": r.match_type,
            }
            for r in results.results
        ]
        return SearchSemanticResult(
            results=chunks,
            total=len(chunks),
            query=query,
            path=project_path,
            engine="hybrid" if hybrid else "vector",
        )
    except Exception as e:
        log.exception("search_failed", project=project_path, error=str(e))
        return error_response("Search failed. Check server logs for details.")


# ---------------------------------------------------------------------------
# MCP Tools (5 semantic tools)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Search the local codebase semantically using natural-language intent. "
        "Use this first for concept discovery (for example: auth flow, retry logic, "
        "error handling path). Requires an absolute repository path and returns ranked matches. "
        "MCP tool call only: do not execute `lgrep_search_semantic` in bash or any shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def search_semantic(
    query: Annotated[
        str | None,
        Field(description="Natural-language intent query (for example: 'JWT verification path')."),
    ] = None,
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root to search."),
    ] = "",
    limit: Annotated[
        int,
        Field(description="Maximum number of ranked matches to return."),
    ] = 10,
    hybrid: bool = True,
    q: str | None = None,
    m: int | None = None,
    ctx: Context | None = None,
) -> SearchSemanticResult | ToolError:
    """Search code semantically using natural language.

    MCP invocation only: call this as a native MCP tool (`lgrep_search_semantic`).
    Do not run `lgrep_search_semantic` as a shell/CLI command via bash.

    Args:
        query: Natural language search query (e.g. "authentication flow", "error handling")
        path: Absolute path to the project to search
        limit: Maximum number of results to return (default: 10)
        hybrid: Use hybrid search combining vector similarity + keyword matching (default: True)
        q: Alias for query (use for shorthand or if query is missing)
        m: Alias for limit (max results)
    """
    query = query or q
    limit = m if m is not None else limit

    log.info("lgrep_search_semantic", query=query, project=path, limit=limit, hybrid=hybrid)

    if not query:
        return error_response("Internal error: query or q is required")

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context
    project_path = str(Path(path).resolve())

    result = await _ensure_search_project_state(app_ctx, path)
    if isinstance(result, dict) and "error" in result:
        return result  # Already a ToolError dict from lifecycle

    state = result

    return await _execute_search(app_ctx, state, query, limit, hybrid, project_path)


@mcp.tool(
    description=(
        "Build or refresh the semantic index for a local repository. "
        "Use when semantic search results are stale or before first-time warmup in new environments. "
        "Requires an absolute local path. MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
)
@time_tool
async def index_semantic(
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root to index."),
    ],
    ctx: Context | None = None,
) -> IndexSemanticResult | ToolError:
    """Index a directory for semantic search.

    Args:
        path: Absolute path to the directory to index

    Returns:
        Indexing status including file count, chunk count, and duration.
    """
    log.info("lgrep_index_semantic", project=path)

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return error_response(f"Path does not exist or is not a directory: {path}")

    # Initialize components if project not yet cached
    result = await _ensure_project_initialized(app_ctx, project_path)
    if isinstance(result, dict):
        return result  # Already a ToolError dict from lifecycle
    state = result

    # Perform indexing
    try:
        status = await asyncio.to_thread(state.indexer.index_all)
        return IndexSemanticResult(
            file_count=status.file_count,
            chunk_count=status.chunk_count,
            duration_ms=round(status.duration_ms, 2),
            total_tokens=status.total_tokens,
        )
    except Exception as e:
        log.exception("indexing_failed", project=str(project_path), error=str(e))
        return error_response("Indexing failed. Check server logs for details.")


@mcp.tool(
    description=(
        "Return semantic index status and stats (files, chunks, watcher state). "
        "Use with a path for one repo, or omit path for all loaded repos. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def status_semantic(
    path: Annotated[
        str,
        Field(description="Optional absolute repository path; omit to list all loaded projects."),
    ] = "",
    ctx: Context | None = None,
) -> StatusSemanticResult | StatusAllProjectsResult | ToolError:
    """Get index status and statistics.

    Args:
        path: Absolute path to project (optional). If omitted, returns stats for all indexed projects.

    Returns:
        Index stats: files, chunks, watching status.
    """
    log.info("lgrep_status_semantic", project=path or "(all)")

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if path:
        # Single-project status
        project_path = str(Path(path).resolve())
        state = app_ctx.projects.get(project_path)

        # Fallback: read stats directly from disk cache (no API key needed)
        if not state:
            if has_disk_cache(project_path):
                log.info("status_reading_disk_cache", project=project_path)
                try:

                    def _read_disk_stats():
                        db_path = get_project_db_path(project_path)
                        store = ChunkStore(db_path, project_path=project_path)
                        chunks = store.count_chunks()
                        files_set = store.get_indexed_files()
                        return len(files_set), chunks

                    file_count, chunk_count = await asyncio.to_thread(_read_disk_stats)
                    return StatusSemanticResult(
                        files=file_count,
                        chunks=chunk_count,
                        watching=False,
                        project=project_path,
                        disk_cache=True,
                        error=None,
                    )
                except Exception as e:
                    log.warning("disk_cache_read_failed", project=project_path, error=str(e))

            return StatusSemanticResult(
                files=0, chunks=0, watching=False, project=project_path, disk_cache=None, error=None
            )

        stats = await _get_project_stats(project_path, state)
        return StatusSemanticResult(
            files=stats.get("files", 0),
            chunks=stats.get("chunks", 0),
            watching=stats.get("watching", False),
            project=stats.get("project", project_path),
            disk_cache=stats.get("disk_cache"),
            error=stats.get("error"),
        )

    # All-projects status
    if not app_ctx.projects:
        return StatusAllProjectsResult(projects=[])

    # Gather stats for all projects concurrently
    tasks = [_get_project_stats(proj_path, state) for proj_path, state in app_ctx.projects.items()]
    projects_status = await asyncio.gather(*tasks)
    return StatusAllProjectsResult(projects=list(projects_status))


@mcp.tool(
    description=(
        "Start semantic file watching for a local repository to keep index data fresh on edits. "
        "Use for long-running sessions where code changes during analysis. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
)
@time_tool
async def watch_start_semantic(
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root to watch."),
    ],
    ctx: Context | None = None,
) -> WatchStartResult | ToolError:
    """Start watching a directory for changes.

    Args:
        path: Absolute path to the directory to watch

    Returns:
        Watching status.
    """
    log.info("lgrep_watch_start_semantic", project=path)

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # 1. Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return error_response(f"Path does not exist or is not a directory: {path}")

    # 2. Initialize project components if needed
    result = await _ensure_project_initialized(app_ctx, project_path)
    if isinstance(result, dict):
        return result  # Already a ToolError dict from lifecycle
    state = result
    path_key = str(project_path)

    # 3. Start watcher
    if state.watching and state.watcher:
        return WatchStartResult(path=path_key, watching=True, message="Already watching")

    try:
        if not state.watcher:
            state.watcher = FileWatcher(state.indexer)

        state.watcher.start()
        state.watching = True
        return WatchStartResult(path=path_key, watching=True, message=None)
    except Exception as e:
        log.exception("watcher_start_failed", project=path_key, error=str(e))
        return error_response("Failed to start watcher. Check server logs for details.")


@mcp.tool(
    description=(
        "Stop semantic file watching for one repository or all repositories. "
        "Use to release watcher resources when active syncing is no longer needed. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
)
@time_tool
async def watch_stop_semantic(
    path: Annotated[
        str,
        Field(description="Optional absolute repository path; omit to stop all active watchers."),
    ] = "",
    ctx: Context | None = None,
) -> WatchStopResult | WatchStopAllResult | ToolError:
    """Stop watching for file changes.

    Args:
        path: Absolute path to project to stop watching (optional). If omitted, stops all watchers.

    Returns:
        Stopped status.
    """
    log.info("lgrep_watch_stop_semantic", project=path or "(all)")

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if path:
        # Stop a specific project's watcher
        project_path = str(Path(path).resolve())
        state = app_ctx.projects.get(project_path)
        if not state or not state.watching or not state.watcher:
            return WatchStopResult(stopped=True, project=None, message="Not watching")

        _stop_watcher(state, project_path)
        return WatchStopResult(stopped=True, project=project_path, message=None)

    # Stop all watchers
    stopped = []
    for proj_path, state in app_ctx.projects.items():
        if _stop_watcher(state, proj_path):
            stopped.append(proj_path)
    return WatchStopAllResult(stopped=True, projects_stopped=stopped)
