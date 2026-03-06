"""lgrep MCP server - Dual-engine code intelligence for OpenCode.

Semantic engine: Voyage Code 3 embeddings with local LanceDB storage.
Symbol engine: tree-sitter AST parsing with JSON index storage.
Supports multiple concurrent projects with isolated indexes.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import structlog
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.embeddings import VoyageEmbedder
from lgrep.indexing import Indexer
from lgrep.storage import ChunkStore, get_project_db_path, has_disk_cache
from lgrep.tools.get_file_outline import get_file_outline as _get_file_outline
from lgrep.tools.get_file_tree import get_file_tree as _get_file_tree
from lgrep.tools.get_repo_outline import get_repo_outline as _get_repo_outline
from lgrep.tools.get_symbol import get_symbol as _get_symbol
from lgrep.tools.get_symbol import get_symbols as _get_symbols

# Symbol tool imports
from lgrep.tools.index_folder import index_folder as _index_folder
from lgrep.tools.index_repo import index_repo as _index_repo
from lgrep.tools.invalidate_cache import invalidate_cache as _invalidate_cache
from lgrep.tools.list_repos import list_repos as _list_repos
from lgrep.tools.search_symbols import search_symbols as _search_symbols
from lgrep.tools.search_text import search_text as _search_text
from lgrep.watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = structlog.get_logger()

# Guard against unbounded memory growth.
# Each project holds a LanceDB connection + potential watcher thread.
MAX_PROJECTS = 20
AUTO_INDEX_MAX_ATTEMPTS = 2
AUTO_INDEX_RETRY_BASE_DELAY_S = 0.1


def time_tool(func):
    """Decorator to time tool execution and log results."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        tool_name = func.__name__
        try:
            result = await func(*args, **kwargs)
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.info(f"{tool_name}_completed", duration_ms=duration)
            return result
        except Exception as e:
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.exception(f"{tool_name}_failed", duration_ms=duration, error=str(e))
            raise

    return wrapper


def _error_response(message: str) -> str:
    """Create a safe JSON error response string.

    Uses json.dumps to ensure valid JSON regardless of message content.
    """
    return json.dumps({"error": message})


@dataclass
class ProjectState:
    """State for a single indexed project."""

    db: ChunkStore
    indexer: Indexer
    watcher: FileWatcher | None = None
    watching: bool = False


@dataclass
class LgrepContext:
    """Application context supporting multiple concurrent projects.

    Each project gets its own ProjectState (ChunkStore, Indexer, FileWatcher),
    keyed by resolved absolute path string. A single VoyageEmbedder is shared
    across all projects to avoid duplicate API client overhead.
    """

    projects: dict[str, ProjectState] = field(default_factory=dict)
    embedder: VoyageEmbedder | None = None
    voyage_api_key: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _indexing_events: dict[str, asyncio.Event] = field(default_factory=dict)


# ============================================================================
# Lifecycle
# ============================================================================


async def _startup(server: FastMCP) -> LgrepContext:
    """Initialize application context and validate environment.

    Returns a fully configured LgrepContext ready for tool calls.
    """
    log.info("lgrep_starting", server=server.name)

    voyage_api_key = os.environ.get("VOYAGE_API_KEY")
    if not voyage_api_key:
        log.error("voyage_api_key_missing", hint="Set VOYAGE_API_KEY env var")

    ctx = LgrepContext(voyage_api_key=voyage_api_key)
    log.info("lgrep_ready")
    return ctx


async def _warm_project(app_ctx: LgrepContext, project_path: Path) -> dict:
    """Warm a single project by loading its disk cache into memory.

    Isolated error handling — never raises.  Returns a status dict
    so the caller can log a summary.
    """
    path_str = str(project_path)
    try:
        result = await _ensure_project_initialized(app_ctx, project_path)
        if isinstance(result, str):
            log.warning("warm_skipped", project=path_str, reason=result)
            return {"path": path_str, "status": "skipped", "detail": result}

        # Start watcher if auto-watch is enabled
        auto_watch = os.environ.get("LGREP_AUTO_WATCH", "").lower() in ("true", "1", "yes")
        if auto_watch and not result.watching:
            from .watcher import FileWatcher

            result.watcher = FileWatcher(result.indexer)
            result.watcher.start()
            result.watching = True
            log.info("auto_watch_started", project=path_str)

        log.info("project_warmed", project=path_str)
        return {"path": path_str, "status": "warmed"}
    except Exception as e:
        log.warning("warm_failed", project=path_str, error=str(e))
        return {"path": path_str, "status": "error", "detail": str(e)}


async def _warm_projects(app_ctx: LgrepContext) -> None:
    """Eagerly load cached indexes listed in ``LGREP_WARM_PATHS``.

    Parses the env var as an ``os.pathsep``-separated list of project
    directories, filters to those that have an existing disk cache, and
    calls ``_ensure_project_initialized`` for each — concurrently.

    Errors in individual projects are logged and skipped; warming never
    blocks server startup.
    """
    raw = os.environ.get("LGREP_WARM_PATHS", "")
    if not raw:
        return

    # Parse, expand, resolve, deduplicate
    seen: set[str] = set()
    paths: list[Path] = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        resolved = Path(entry).expanduser().resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if not resolved.is_dir():
            log.warning("warm_path_not_directory", path=key)
            continue
        if not has_disk_cache(key):
            log.info("warm_no_disk_cache", path=key)
            continue
        paths.append(resolved)

    if not paths:
        return

    # Respect MAX_PROJECTS — existing projects count toward the cap
    available = max(0, MAX_PROJECTS - len(app_ctx.projects))
    if len(paths) > available:
        log.warning(
            "warm_paths_capped",
            requested=len(paths),
            available=available,
            max=MAX_PROJECTS,
        )
        paths = paths[:available]

    results = await asyncio.gather(
        *[_warm_project(app_ctx, p) for p in paths],
        return_exceptions=True,
    )

    warmed = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "warmed")
    log.info("warm_complete", warmed=warmed, total=len(paths))


async def _shutdown(ctx: LgrepContext) -> None:
    """Gracefully shut down all projects: stop watchers and release resources."""
    log.info("lgrep_shutdown", project_count=len(ctx.projects))

    for proj_path, state in ctx.projects.items():
        _stop_watcher(state, proj_path)

    ctx.projects.clear()
    ctx.embedder = None
    log.info("lgrep_shutdown_complete")


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[LgrepContext]:
    """Manage application lifecycle with optional eager warming."""
    ctx = await _startup(server)
    await _warm_projects(ctx)
    try:
        yield ctx
    finally:
        await _shutdown(ctx)


# Create the MCP server with lifespan
mcp = FastMCP(
    "lgrep",
    lifespan=app_lifespan,
)


# ============================================================================
# Helpers
# ============================================================================


def _stop_watcher(state: ProjectState, project_path: str) -> bool:
    """Stop a project's file watcher and reset its state.

    Returns True if a watcher was actually stopped, False if nothing to stop.
    """
    if not state.watcher or not state.watching:
        return False

    try:
        state.watcher.stop()
    except Exception as e:
        log.error("watcher_stop_failed", project=project_path, error=str(e))
    finally:
        state.watching = False
        state.watcher = None

    return True


async def _ensure_project_initialized(
    app_ctx: LgrepContext, project_path: Path
) -> ProjectState | str:
    """Look up or create a ProjectState for the given path.

    Uses double-checked locking: fast lock-free path for already-cached projects,
    asyncio.Lock only for first-time initialization.

    Returns ProjectState on success, or a JSON error string on failure.
    """
    path_key = str(project_path)

    # Fast path: already initialized (no lock needed)
    if path_key in app_ctx.projects:
        return app_ctx.projects[path_key]

    # Slow path: need to create (under lock to prevent duplicate entries)
    async with app_ctx._lock:
        # Double-check after acquiring lock
        if path_key in app_ctx.projects:
            return app_ctx.projects[path_key]

        # Check MAX_PROJECTS limit
        count = len(app_ctx.projects)
        if count >= MAX_PROJECTS:
            return _error_response(
                f"Maximum project limit ({MAX_PROJECTS}) reached. "
                "Restart the server or use the CLI to evict unused projects."
            )
        if count >= int(MAX_PROJECTS * 0.8):
            log.warning("approaching_project_limit", current=count, max=MAX_PROJECTS)

        if not app_ctx.voyage_api_key:
            return _error_response("VOYAGE_API_KEY not set.")

        try:
            # Create shared embedder on first use
            if app_ctx.embedder is None:
                app_ctx.embedder = VoyageEmbedder(api_key=app_ctx.voyage_api_key)

            db_path = get_project_db_path(project_path)
            db = ChunkStore(db_path)
            indexer = Indexer(
                project_path=project_path,
                storage=db,
                embedder=app_ctx.embedder,
            )
            state = ProjectState(db=db, indexer=indexer)
            app_ctx.projects[path_key] = state
            log.info("project_initialized", project=path_key)
            return state
        except Exception as e:
            log.exception("initialization_failed", project=path_key, error=str(e))
            return _error_response("Failed to initialize project.")


async def _get_project_stats(proj_path: str, state: ProjectState) -> dict:
    """Get stats for a single project. Safe to call concurrently via asyncio.gather."""
    try:
        chunks = await asyncio.to_thread(state.db.count_chunks)
        files_set = await asyncio.to_thread(state.db.get_indexed_files)
        return {
            "files": len(files_set),
            "chunks": chunks,
            "watching": state.watching,
            "project": proj_path,
        }
    except Exception as e:
        log.exception("status_failed", project=proj_path, error=str(e))
        return {
            "files": 0,
            "chunks": 0,
            "watching": False,
            "project": proj_path,
            "error": str(e),
        }


async def _finish_single_flight_indexing(
    app_ctx: LgrepContext, project_path: str, event: asyncio.Event
) -> None:
    """Release single-flight state and notify any waiting followers."""
    async with app_ctx._lock:
        if app_ctx._indexing_events.get(project_path) is event:
            app_ctx._indexing_events.pop(project_path, None)
    event.set()


async def _auto_index_project_single_flight(
    app_ctx: LgrepContext, project_path: str, path_obj: Path
) -> ProjectState | str:
    """Auto-index project on first search using leader/follower coordination."""
    is_leader = False
    async with app_ctx._lock:
        if project_path in app_ctx._indexing_events:
            event = app_ctx._indexing_events[project_path]
        else:
            event = asyncio.Event()
            app_ctx._indexing_events[project_path] = event
            is_leader = True

    if not is_leader:
        log.info("search_auto_index_waiting", project=project_path)
        await event.wait()
        state = app_ctx.projects.get(project_path)
        if not state:
            return _error_response(
                "Auto-indexing by a concurrent request failed. Retry your search."
            )
        return state

    log.info("search_auto_index_start", project=project_path)
    try:
        result = await _ensure_project_initialized(app_ctx, path_obj)
        if isinstance(result, str):
            return result
        state = result

        for attempt in range(1, AUTO_INDEX_MAX_ATTEMPTS + 1):
            try:
                status = await asyncio.to_thread(state.indexer.index_all)
                log.info(
                    "search_auto_index_success",
                    project=project_path,
                    files=status.file_count,
                    chunks=status.chunk_count,
                    duration_ms=round(status.duration_ms, 2),
                    attempt=attempt,
                    max_attempts=AUTO_INDEX_MAX_ATTEMPTS,
                )
                return state
            except Exception as e:
                if attempt < AUTO_INDEX_MAX_ATTEMPTS:
                    delay_s = AUTO_INDEX_RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                    log.warning(
                        "search_auto_index_retry",
                        project=project_path,
                        attempt=attempt,
                        max_attempts=AUTO_INDEX_MAX_ATTEMPTS,
                        delay_s=delay_s,
                        error=str(e),
                    )
                    await asyncio.sleep(delay_s)
                    continue

                app_ctx.projects.pop(project_path, None)
                log.exception(
                    "search_auto_index_failed",
                    project=project_path,
                    attempts=AUTO_INDEX_MAX_ATTEMPTS,
                    error=str(e),
                )
                return _error_response(
                    "Failed to auto-index project on first search. Check server logs for details."
                )
    except Exception as e:
        app_ctx.projects.pop(project_path, None)
        log.exception("search_auto_index_failed", project=project_path, error=str(e))
        return _error_response(
            "Failed to auto-index project on first search. Check server logs for details."
        )
    finally:
        await _finish_single_flight_indexing(app_ctx, project_path, event)


async def _ensure_search_project_state(app_ctx: LgrepContext, path: str) -> ProjectState | str:
    """Resolve project path and ensure a ready ProjectState for search."""
    project_path = str(Path(path).resolve())
    state = app_ctx.projects.get(project_path)
    if state:
        return state

    if has_disk_cache(project_path):
        log.info("search_auto_loading_from_disk", project=project_path)
        result = await _ensure_project_initialized(app_ctx, Path(project_path))
        return result

    path_obj = Path(project_path)
    if not path_obj.exists() or not path_obj.is_dir():
        return _error_response(f"Path does not exist or is not a directory: {path}")

    return await _auto_index_project_single_flight(app_ctx, project_path, path_obj)


async def _execute_search(
    app_ctx: LgrepContext,
    state: ProjectState,
    query: str,
    limit: int,
    hybrid: bool,
    project_path: str,
) -> str:
    """Run embedding + storage search and return JSON output."""
    try:
        query_vector = await asyncio.to_thread(app_ctx.embedder.embed_query, query)
        if hybrid:
            results = await asyncio.to_thread(state.db.search_hybrid, query_vector, query, limit)
        else:
            results = await asyncio.to_thread(state.db.search_vector, query_vector, limit)
        return json.dumps(asdict(results))
    except Exception as e:
        log.exception("search_failed", project=project_path, error=str(e))
        return _error_response("Search failed. Check server logs for details.")


# ============================================================================
# MCP Tools
# ============================================================================


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
) -> str:
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
        return _error_response("Internal error: query or q is required")

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context
    project_path = str(Path(path).resolve())

    result = await _ensure_search_project_state(app_ctx, path)
    if isinstance(result, str):
        return result
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
) -> str:
    """Index a directory for semantic search.

    Args:
        path: Absolute path to the directory to index

    Returns:
        JSON with indexing status including file count, chunk count, and duration.
    """
    log.info("lgrep_index_semantic", project=path)

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return _error_response(f"Path does not exist or is not a directory: {path}")

    # Initialize components if project not yet cached
    result = await _ensure_project_initialized(app_ctx, project_path)
    if isinstance(result, str):
        return result
    state = result

    # Perform indexing
    try:
        status = await asyncio.to_thread(state.indexer.index_all)
        return json.dumps(
            {
                "file_count": status.file_count,
                "chunk_count": status.chunk_count,
                "duration_ms": round(status.duration_ms, 2),
                "total_tokens": status.total_tokens,
            }
        )
    except Exception as e:
        log.exception("indexing_failed", project=str(project_path), error=str(e))
        return _error_response("Indexing failed. Check server logs for details.")


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
) -> str:
    """Get index status and statistics.

    Args:
        path: Absolute path to project (optional). If omitted, returns stats for all indexed projects.

    Returns:
        JSON with index stats: files, chunks, watching status.
    """
    log.info("lgrep_status_semantic", project=path or "(all)")

    if not ctx:
        return _error_response("Internal error: Context missing")

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
                    db_path = get_project_db_path(project_path)
                    store = ChunkStore(db_path)
                    chunks = store.count_chunks()
                    files_set = store.get_indexed_files()
                    return json.dumps(
                        {
                            "files": len(files_set),
                            "chunks": chunks,
                            "watching": False,
                            "project": project_path,
                            "disk_cache": True,
                        }
                    )
                except Exception as e:
                    log.warning("disk_cache_read_failed", project=project_path, error=str(e))

            return json.dumps({"files": 0, "chunks": 0, "watching": False, "project": project_path})

        stats = await _get_project_stats(project_path, state)
        return json.dumps(stats)

    # All-projects status
    if not app_ctx.projects:
        return json.dumps({"projects": []})

    # Gather stats for all projects concurrently
    tasks = [_get_project_stats(proj_path, state) for proj_path, state in app_ctx.projects.items()]
    projects_status = await asyncio.gather(*tasks)
    return json.dumps({"projects": list(projects_status)})


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
) -> str:
    """Start watching a directory for changes.

    Args:
        path: Absolute path to the directory to watch

    Returns:
        JSON with watching status.
    """
    log.info("lgrep_watch_start_semantic", project=path)

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # 1. Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return _error_response(f"Path does not exist or is not a directory: {path}")

    # 2. Initialize project components if needed
    result = await _ensure_project_initialized(app_ctx, project_path)
    if isinstance(result, str):
        return result
    state = result
    path_key = str(project_path)

    # 3. Start watcher
    if state.watching and state.watcher:
        return json.dumps(
            {
                "path": path_key,
                "watching": True,
                "message": "Already watching",
            }
        )

    try:
        if not state.watcher:
            state.watcher = FileWatcher(state.indexer)

        state.watcher.start()
        state.watching = True
        return json.dumps({"path": path_key, "watching": True})
    except Exception as e:
        log.exception("watcher_start_failed", project=path_key, error=str(e))
        return _error_response("Failed to start watcher. Check server logs for details.")


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
) -> str:
    """Stop watching for file changes.

    Args:
        path: Absolute path to project to stop watching (optional). If omitted, stops all watchers.

    Returns:
        JSON with stopped status.
    """
    log.info("lgrep_watch_stop_semantic", project=path or "(all)")

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if path:
        # Stop a specific project's watcher
        project_path = str(Path(path).resolve())
        state = app_ctx.projects.get(project_path)
        if not state or not state.watching or not state.watcher:
            return json.dumps({"stopped": True, "message": "Not watching"})

        _stop_watcher(state, project_path)
        return json.dumps({"stopped": True, "project": project_path})

    # Stop all watchers
    stopped = []
    for proj_path, state in app_ctx.projects.items():
        if _stop_watcher(state, proj_path):
            stopped.append(proj_path)
    return json.dumps({"stopped": True, "projects_stopped": stopped})


# ============================================================================
# Symbol Tools (11 new tools)
# ============================================================================


@mcp.tool(
    description=(
        "Index symbols from a local repository for exact symbol lookup tools. "
        "Run before symbol search/get operations, then refresh after major code changes. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
)
@time_tool
async def index_symbols_folder(
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root for symbol indexing."),
    ],
    max_files: Annotated[
        int,
        Field(description="Maximum files to parse during this indexing run."),
    ] = 500,
    incremental: Annotated[
        bool,
        Field(description="Skip unchanged files when true; set false to force full rebuild."),
    ] = True,
) -> str:
    """Index all symbols in a local folder for exact symbol lookup.

    Args:
        path: Absolute path to the repository/folder root
        max_files: Maximum number of files to index (default: 500)
        incremental: Skip files whose content hash matches the stored index
                     (default: True). Set to False to force a full re-index.

    Returns:
        JSON with files_indexed, files_skipped, symbols_indexed, repo_path,
        and _meta envelope
    """
    result = _index_folder(path, max_files=max_files, incremental=incremental)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Index symbols directly from a GitHub repository via API without cloning locally. "
        "Use for remote code exploration when a local checkout is unavailable. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
)
@time_tool
async def index_symbols_repo(
    repo: Annotated[
        str,
        Field(description="GitHub repository in owner/name format (for example: anomalyco/lgrep)."),
    ],
    ref: Annotated[
        str,
        Field(description="Branch, tag, or commit SHA to index."),
    ] = "HEAD",
    max_files: Annotated[
        int,
        Field(description="Maximum files to fetch and parse from the remote repository."),
    ] = 500,
    github_token: Annotated[
        str | None,
        Field(description="Optional token for private repos or higher GitHub API limits."),
    ] = None,
) -> str:
    """Index symbols from a GitHub repository via the REST API (no git clone).

    Args:
        repo: GitHub repo in "owner/name" format (e.g. "anomalyco/lgrep")
        ref: Branch, tag, or commit SHA to index (default: "HEAD")
        max_files: Maximum number of files to index (default: 500)
        github_token: Optional GitHub personal access token for private repos

    Returns:
        JSON with files_indexed, symbols_indexed, repo, and _meta envelope
    """
    result = await _index_repo(repo, ref=ref, max_files=max_files, github_token=github_token)
    return json.dumps(result)


@mcp.tool(
    description=(
        "List repositories currently indexed in the symbol store. "
        "Use to discover available repository keys before symbol queries. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def list_repos() -> str:
    """List all repositories that have been indexed in the symbol store.

    Returns:
        JSON with repos list and _meta envelope
    """
    result = _list_repos()
    return json.dumps(result)


@mcp.tool(
    description=(
        "Return repository file tree with ignore rules applied. "
        "Use for quick structural navigation before deeper symbol or semantic queries. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_file_tree(
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root."),
    ],
    max_files: Annotated[
        int,
        Field(description="Maximum file paths to return in the tree response."),
    ] = 500,
) -> str:
    """Get the file tree of a repository, respecting .gitignore.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to return (default: 500)

    Returns:
        JSON with files list (relative paths), total_files, and _meta envelope
    """
    result = _get_file_tree(path, max_files=max_files)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Return symbol outline for a single source file (functions, classes, methods). "
        "Works without prior indexing and is ideal for file-level orientation. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_file_outline(
    path: Annotated[
        str,
        Field(description="Absolute path to the source file to outline."),
    ],
    repo_root: Annotated[
        str | None,
        Field(description="Optional repository root for stable relative symbol identifiers."),
    ] = None,
) -> str:
    """Get the symbol outline (functions, classes, methods) for a single file.

    Args:
        path: Absolute path to the source file
        repo_root: Optional repo root for relative path computation in symbol IDs

    Returns:
        JSON with file_path, symbols list, symbol_count, and _meta envelope
    """
    result = _get_file_outline(path, repo_root=repo_root)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Return symbol outlines across a repository for structural understanding. "
        "Use this for architecture mapping or broad navigation of code entities. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_repo_outline(
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root."),
    ],
    max_files: Annotated[
        int,
        Field(description="Maximum number of files to scan while building outlines."),
    ] = 500,
) -> str:
    """Get the symbol outline across an entire repository.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        JSON with repo_path, files list, total_files, total_symbols, and _meta envelope
    """
    result = _get_repo_outline(path, max_files=max_files)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Search indexed code symbols by name (functions, classes, methods, interfaces). "
        "Use this for exact symbol lookup after indexing with `lgrep_index_symbols_folder`. "
        "Requires an absolute repository path and supports optional symbol-kind filtering. "
        "MCP tool call only: do not execute `lgrep_search_symbols` in bash or any shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def search_symbols(
    query: Annotated[
        str,
        Field(description="Symbol name query (case-insensitive substring match)."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the indexed repository root."),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of symbol matches to return."),
    ] = 20,
    kind: Annotated[
        str | None,
        Field(description="Optional symbol kind filter (for example: function, class, method)."),
    ] = None,
) -> str:
    """Search for symbols by name in an indexed repository.

    MCP invocation only: call this as a native MCP tool (`lgrep_search_symbols`).
    Do not run `lgrep_search_symbols` as a shell/CLI command via bash.

    Performs case-insensitive substring matching on symbol names.
    Run lgrep_index_symbols_folder first to build the index.

    Args:
        query: Search query (matched against symbol names)
        path: Absolute path to the indexed repository
        limit: Maximum number of results to return (default: 20)
        kind: Optional filter by symbol kind (function, class, method, etc.)

    Returns:
        JSON with results list, total_matches, and _meta envelope
    """
    result = _search_symbols(query, path, limit=limit, kind=kind)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Search literal text occurrences across local source files. "
        "Use for exact token/identifier matching when semantic intent is not required. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def search_text(
    query: Annotated[
        str,
        Field(description="Literal text to search for."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the local repository root."),
    ],
    max_results: Annotated[
        int,
        Field(description="Maximum number of text matches to return."),
    ] = 50,
    case_sensitive: Annotated[
        bool,
        Field(description="When true, match text with case sensitivity."),
    ] = False,
) -> str:
    """Search for literal text across all source files in a repository.

    Args:
        query: Text to search for
        path: Absolute path to the repository root
        max_results: Maximum number of results to return (default: 50)
        case_sensitive: Whether to perform case-sensitive matching (default: False)

    Returns:
        JSON with results list (file_path, line_number, line) and _meta envelope
    """
    result = _search_text(query, path, max_results=max_results, case_sensitive=case_sensitive)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Get full metadata and source for one symbol ID from the symbol index. "
        "Use after symbol search to inspect a specific implementation body. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_symbol(
    symbol_id: Annotated[
        str,
        Field(description="Stable symbol ID in file_path:kind:name format."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the indexed local repository root."),
    ],
) -> str:
    """Get full metadata and source code for a single symbol by ID.

    Symbol IDs use the format "file_path:kind:name" (e.g. "src/auth.py:function:authenticate").
    Run lgrep_index_symbols_folder first to build the index.

    Args:
        symbol_id: Stable symbol ID in format "file_path:kind:name"
        path: Absolute path to the indexed repository

    Returns:
        JSON with symbol dict (including source field) and _meta envelope
    """
    result = _get_symbol(symbol_id, path)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Batch-get metadata and source for multiple symbol IDs in a single request. "
        "Use to reduce round-trips after search results provide several relevant IDs. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_symbols(
    symbol_ids: Annotated[
        list[str],
        Field(description="List of symbol IDs in file_path:kind:name format."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the indexed local repository root."),
    ],
) -> str:
    """Get full metadata and source code for multiple symbols in one call.

    Args:
        symbol_ids: List of stable symbol IDs (format: "file_path:kind:name")
        path: Absolute path to the indexed repository

    Returns:
        JSON with symbols list and _meta envelope
    """
    result = _get_symbols(symbol_ids, path)
    return json.dumps(result)


@mcp.tool(
    description=(
        "Delete a repository symbol index cache entry to force a full symbol re-index. "
        "Use when index corruption or schema drift is suspected. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@time_tool
async def invalidate_cache(
    path: Annotated[
        str,
        Field(
            description="Absolute path to the local repository root whose symbol cache should be removed."
        ),
    ],
) -> str:
    """Remove the symbol index for a repository, forcing a full re-index on next use.

    Args:
        path: Absolute path to the repository root

    Returns:
        JSON with status ("deleted" or "not_found") and _meta envelope
    """
    result = _invalidate_cache(path)
    return json.dumps(result)


def remove_project(app_ctx: LgrepContext, path: str) -> dict:
    """Remove a project from server memory, freeing its resource slot.

    NOT exposed as an MCP tool — called from CLI only to avoid
    adding token overhead and confusing AI agents.

    Stops the file watcher (if active) and removes the project from memory.
    The on-disk LanceDB index is preserved and will be reused if the project
    is re-indexed later.

    Args:
        app_ctx: The application context.
        path: Absolute path to the project to remove.

    Returns:
        Dict with removal status.
    """
    log.info("lgrep_remove", project=path)

    project_path = str(Path(path).resolve())
    state = app_ctx.projects.get(project_path)
    if not state:
        return {"removed": False, "message": "Project not loaded", "project": project_path}

    # Stop watcher if active
    _stop_watcher(state, project_path)

    # Remove from dict
    del app_ctx.projects[project_path]
    log.info("project_removed", project=project_path, remaining=len(app_ctx.projects))

    return {
        "removed": True,
        "project": project_path,
        "remaining_projects": len(app_ctx.projects),
    }


# ============================================================================
# Entry Point
# ============================================================================


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 6285) -> int:
    """Start the MCP server.

    Args:
        transport: Transport protocol - "stdio" or "streamable-http".
        host: Host to bind to (only for HTTP transport).
        port: Port to bind to (only for HTTP transport).
    """
    # Configure structlog for JSON output
    log_level = getattr(
        logging,
        os.environ.get("LGREP_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
    )

    log.info("lgrep_mcp_server_starting", transport=transport, host=host, port=port)

    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(run_server())
