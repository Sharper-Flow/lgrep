"""Semantic tools: search, index, status, watch for the lgrep MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import Context  # noqa: TC002
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.chunking import strip_split_breadcrumb
from lgrep.server import log, mcp, time_tool
from lgrep.server.lifecycle import (
    LgrepContext,
    ProjectState,
    _check_staleness,
    _ensure_project_initialized,
    _ensure_search_project_state,
    _get_project_stats,
    _schedule_background_reindex,
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
from lgrep.storage import has_disk_cache, open_checkout_store
from lgrep.watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _run_blocking(
    app_ctx: LgrepContext,
    kind: str,
    caller: str,
    project: str | None,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run sync daemon work through the bounded runtime supervisor."""
    return await app_ctx.runtime.run_blocking(kind, caller, project, fn, *args, **kwargs)


def _cheap_project_status(proj_path: str, state: ProjectState) -> StatusSemanticResult:
    """Return memory-only status for no-arg global status calls."""
    return StatusSemanticResult(
        files=0,
        chunks=0,
        watching=state.watching,
        project=proj_path,
        disk_cache=None,
        error=None,
        summary_only=True,
        detail="Counts omitted for cheap global status; call with path for deep counts.",
    )


def _chunk_snippet(content: str, max_lines: int = 3, max_chars: int = 120) -> str:
    """First non-blank chunk-body lines, each capped, joined by newlines.

    Chonkie's breadcrumb-form header is removed first, so later chunks of
    a split class start at their body. The plain header form on the
    first chunk of a split class is kept: stored text alone cannot tell
    it from two verbatim adjacent definitions, and the kept line is the
    enclosing declaration from the same file.
    """
    body = strip_split_breadcrumb(content)
    lines: list[str] = []
    for line in body.split("\n"):
        if line.strip():
            lines.append(line[:max_chars])
            if len(lines) == max_lines:
                break
    return "\n".join(lines)


async def _execute_search(
    app_ctx: LgrepContext,
    state: ProjectState,
    query: str,
    limit: int,
    hybrid: bool,
    project_path: str,
    include_content: bool = False,
) -> SearchSemanticResult | ToolError:
    """Run embedding + storage search and return structured result."""
    if app_ctx.embedder is None:
        return error_response("VOYAGE_API_KEY not set. Cannot perform semantic search.")
    try:
        # Auto-staleness pre-flight: cheap mtime gate, then hash check on the
        # suspect subset only. If drift is confirmed, re-index synchronously
        # before paying for the query embedding so the user sees fresh results.
        stale, suspect_count = await _run_blocking(
            app_ctx,
            "staleness_check",
            "_execute_search",
            project_path,
            _check_staleness,
            state,
        )
        line_repair_done = await _run_blocking(
            app_ctx,
            "line_repair_state",
            "_execute_search",
            project_path,
            state.db.line_repair_done,
        )
        if stale or not line_repair_done:
            log.info(
                "staleness_triggered_background_reindex",
                project=project_path,
                suspect_count=suspect_count,
                line_repair_pending=not line_repair_done,
            )
            await _schedule_background_reindex(app_ctx, project_path, Path(project_path))
            # Serve the current (possibly stale) index immediately. The reindex
            # itself is NOT awaited — only the cheap scheduler (lock + create_task).

        query_vector = await app_ctx.embedder.embed_query_async(query)
        if hybrid:
            results = await _run_blocking(
                app_ctx,
                "search_hybrid",
                "_execute_search",
                project_path,
                state.db.search_hybrid,
                query_vector,
                query,
                limit,
            )
        else:
            results = await _run_blocking(
                app_ctx,
                "search_vector",
                "_execute_search",
                project_path,
                state.db.search_vector,
                query_vector,
                limit,
            )
        # Explicit key mapping: compact hits by default — path, correct
        # line range, score, match type, and a short snippet. The full
        # stored chunk text rides along only when requested.
        chunks = [
            {
                "file_path": r.file_path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "score": r.score,
                "match_type": r.match_type,
                "snippet": _chunk_snippet(r.content),
                **({"content": r.content} if include_content else {}),
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
        str,
        Field(description="Natural-language intent query (for example: 'JWT verification path')."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root to search."),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of ranked matches to return."),
    ] = 10,
    hybrid: bool = True,
    include_content: Annotated[
        bool,
        Field(description="Add the full stored chunk text as content on each hit."),
    ] = False,
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
        include_content: Add the full stored chunk text as content on each
            hit. Default hits stay compact: file_path, start_line, end_line,
            score, match_type, and a 3-line snippet.
    """
    log.info(
        "lgrep_search_semantic",
        query=query,
        project=path,
        limit=limit,
        hybrid=hybrid,
        include_content=include_content,
    )

    if not query:
        return error_response("Internal error: query is required")

    if not ctx:
        return error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context
    project_path = str(Path(path).resolve())

    result = await _ensure_search_project_state(app_ctx, path)
    if isinstance(result, dict) and "error" in result:
        return result  # Already a ToolError dict from lifecycle

    state = result

    return await _execute_search(
        app_ctx, state, query, limit, hybrid, project_path, include_content
    )


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
        base_tokens = 0
        if state.base_path is not None:
            # A worktree overlay holds only what differs from its trunk's base
            # rows, so bring the base current before comparing against it.
            base = await _ensure_project_initialized(app_ctx, Path(state.base_path))
            if not isinstance(base, dict):
                base_status = await _run_blocking(
                    app_ctx, "index_all", "index_semantic", state.base_path, base.indexer.index_all
                )
                base_tokens = base_status.total_tokens
                base.latest_indexed_at = None
        status = await _run_blocking(
            app_ctx,
            "index_all",
            "index_semantic",
            str(project_path),
            state.indexer.index_all,
        )
        # Refresh cached freshness timestamp so staleness pre-flight sees the
        # just-rebuilt index on the next search call.
        state.latest_indexed_at = await _run_blocking(
            app_ctx,
            "db_latest_indexed_at",
            "index_semantic",
            str(project_path),
            state.db.get_latest_indexed_at,
        )
        return IndexSemanticResult(
            file_count=status.file_count,
            chunk_count=status.chunk_count,
            duration_ms=round(status.duration_ms, 2),
            total_tokens=status.total_tokens + base_tokens,
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
                        store = open_checkout_store(project_path)
                        chunks = store.count_chunks()
                        files_set = store.get_indexed_files()
                        return len(files_set), chunks

                    file_count, chunk_count = await _run_blocking(
                        app_ctx,
                        "status_disk_cache_stats",
                        "status_semantic",
                        project_path,
                        _read_disk_stats,
                    )
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

        stats = await _get_project_stats(project_path, state, app_ctx.runtime)
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

    # Cheap memory-only summary: no global LanceDB/database fanout by default.
    projects_status = [
        _cheap_project_status(proj_path, state) for proj_path, state in app_ctx.projects.items()
    ]
    return StatusAllProjectsResult(projects=projects_status)


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
            state.watcher = FileWatcher(state.indexer, runtime=app_ctx.runtime)

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
