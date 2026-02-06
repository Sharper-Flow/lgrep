"""lgrep MCP server - Semantic code search for OpenCode.

Uses Voyage Code 3 embeddings with local LanceDB storage.
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from mcp.server.fastmcp import Context, FastMCP

from lgrep.embeddings import VoyageEmbedder
from lgrep.indexing import Indexer
from lgrep.storage import ChunkStore, get_project_db_path
from lgrep.watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    pass

log = structlog.get_logger()


def time_tool(func):
    """Decorator to time tool execution and log results."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        tool_name = func.__name__
        try:
            result = await func(*args, **kwargs)
            duration = (time.perf_counter() - start) * 1000
            log.info(f"{tool_name}_completed", duration_ms=duration)
            return result
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            log.exception(f"{tool_name}_failed", duration_ms=duration, error=str(e))
            raise

    return wrapper


def _error_response(message: str) -> str:
    """Create a safe JSON error response string.

    Uses json.dumps to ensure valid JSON regardless of message content.
    """
    return json.dumps({"error": message})


@dataclass
class LgrepContext:
    """Application context with warm connections."""

    db: ChunkStore | None = None
    embedder: VoyageEmbedder | None = None
    indexer: Indexer | None = None
    watcher: FileWatcher | None = None
    voyage_api_key: str | None = None
    watching: bool = False
    project_path: str | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[LgrepContext]:
    """Manage application lifecycle with warm connections."""
    log.info("lgrep_starting", server=server.name)

    # Get API key from environment
    voyage_api_key = os.environ.get("VOYAGE_API_KEY")
    if not voyage_api_key:
        log.error("voyage_api_key_missing", hint="Set VOYAGE_API_KEY env var")

    ctx = LgrepContext(
        voyage_api_key=voyage_api_key,
    )

    try:
        log.info("lgrep_ready")
        yield ctx
    finally:
        log.info("lgrep_shutdown")
        if ctx.watcher:
            ctx.watcher.stop()


# Create the MCP server with lifespan
mcp = FastMCP(
    "lgrep",
    lifespan=app_lifespan,
)


# ============================================================================
# Helpers
# ============================================================================


def _ensure_project_initialized(app_ctx: LgrepContext, project_path: Path) -> str | None:
    """Initialize project components if the project changed or is first-time.

    Returns None on success, or a JSON error string on failure.
    """
    if app_ctx.project_path == str(project_path):
        return None

    log.info("initializing_project", path=str(project_path))

    if not app_ctx.voyage_api_key:
        return _error_response("VOYAGE_API_KEY not set.")

    try:
        db_path = get_project_db_path(project_path)
        app_ctx.db = ChunkStore(db_path)
        app_ctx.embedder = VoyageEmbedder(api_key=app_ctx.voyage_api_key)
        app_ctx.indexer = Indexer(
            project_path=project_path,
            storage=app_ctx.db,
            embedder=app_ctx.embedder,
        )
        app_ctx.project_path = str(project_path)
    except Exception as e:
        log.exception("initialization_failed", error=str(e))
        return _error_response("Failed to initialize project.")

    return None


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool()
@time_tool
async def lgrep_search(
    query: str,
    limit: int = 10,
    hybrid: bool = True,
    ctx: Context | None = None,
) -> str:
    """Search code semantically using natural language.

    Args:
        query: Natural language search query (e.g. "authentication flow", "error handling")
        limit: Maximum number of results to return (default: 10)
        hybrid: Use hybrid search combining vector similarity + keyword matching (default: True)

    Returns:
        JSON with search results including file paths, line numbers, and code snippets.
    """
    log.info("lgrep_search", query=query, limit=limit, hybrid=hybrid)

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if not app_ctx.db or not app_ctx.embedder:
        return _error_response("No project indexed. Call lgrep_index first.")

    try:
        # 1. Embed query (run in thread to avoid blocking event loop)
        query_vector = await asyncio.to_thread(app_ctx.embedder.embed_query, query)

        # 2. Search (run in thread - LanceDB I/O)
        if hybrid:
            results = await asyncio.to_thread(app_ctx.db.search_hybrid, query_vector, query, limit)
        else:
            results = await asyncio.to_thread(app_ctx.db.search_vector, query_vector, limit)

        # 3. Format response
        return json.dumps(asdict(results))
    except Exception as e:
        log.exception("search_failed", error=str(e))
        return _error_response("Search failed. Check server logs for details.")


@mcp.tool()
@time_tool
async def lgrep_index(
    path: str,
    ctx: Context | None = None,
) -> str:
    """Index a directory for semantic search.

    Args:
        path: Absolute path to the directory to index

    Returns:
        JSON with indexing status including file count, chunk count, and duration.
    """
    log.info("lgrep_index", path=path)

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return _error_response(f"Path does not exist or is not a directory: {path}")

    # Initialize components if project changed or first time
    init_error = _ensure_project_initialized(app_ctx, project_path)
    if init_error:
        return init_error

    # Perform indexing
    try:
        status = await asyncio.to_thread(app_ctx.indexer.index_all)
        return json.dumps(
            {
                "file_count": status.file_count,
                "chunk_count": status.chunk_count,
                "duration_ms": round(status.duration_ms, 2),
                "total_tokens": status.total_tokens,
            }
        )
    except Exception as e:
        log.exception("indexing_failed", error=str(e))
        return _error_response("Indexing failed. Check server logs for details.")


@mcp.tool()
@time_tool
async def lgrep_status(ctx: Context | None = None) -> str:
    """Get index status and statistics.

    Returns:
        JSON with index stats: files, chunks, last_updated, watching status.
    """
    log.info("lgrep_status")

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if not app_ctx.db:
        return json.dumps({"files": 0, "chunks": 0, "watching": False, "project": None})

    try:
        chunks = await asyncio.to_thread(app_ctx.db.count_chunks)
        files_set = await asyncio.to_thread(app_ctx.db.get_indexed_files)
        files = len(files_set)

        return json.dumps(
            {
                "files": files,
                "chunks": chunks,
                "watching": app_ctx.watching,
                "project": app_ctx.project_path,
            }
        )
    except Exception as e:
        log.exception("status_failed", error=str(e))
        return _error_response("Failed to get status. Check server logs for details.")


@mcp.tool()
@time_tool
async def lgrep_watch_start(
    path: str,
    ctx: Context | None = None,
) -> str:
    """Start watching a directory for changes.

    Args:
        path: Absolute path to the directory to watch

    Returns:
        JSON with watching status.
    """
    log.info("lgrep_watch_start", path=path)

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    # 1. Validate path
    project_path = Path(path).resolve()
    if not project_path.exists() or not project_path.is_dir():
        return _error_response(f"Path does not exist or is not a directory: {path}")

    # 2. Initialize project components if needed
    init_error = _ensure_project_initialized(app_ctx, project_path)
    if init_error:
        return init_error

    # 3. Start watcher
    if app_ctx.watching and app_ctx.watcher:
        return json.dumps(
            {
                "path": app_ctx.project_path,
                "watching": True,
                "message": "Already watching",
            }
        )

    try:
        if not app_ctx.watcher:
            app_ctx.watcher = FileWatcher(app_ctx.indexer)

        app_ctx.watcher.start()
        app_ctx.watching = True
        return json.dumps({"path": app_ctx.project_path, "watching": True})
    except Exception as e:
        log.exception("watcher_start_failed", error=str(e))
        return _error_response("Failed to start watcher. Check server logs for details.")


@mcp.tool()
@time_tool
async def lgrep_watch_stop(ctx: Context | None = None) -> str:
    """Stop watching for file changes.

    Returns:
        JSON with stopped status.
    """
    log.info("lgrep_watch_stop")

    if not ctx:
        return _error_response("Internal error: Context missing")

    app_ctx: LgrepContext = ctx.request_context.lifespan_context

    if not app_ctx.watching or not app_ctx.watcher:
        return json.dumps({"stopped": True, "message": "Not watching"})

    try:
        app_ctx.watcher.stop()
        app_ctx.watching = False
        app_ctx.watcher = None
        return json.dumps({"stopped": True})
    except Exception as e:
        log.exception("watcher_stop_failed", error=str(e))
        return _error_response("Failed to stop watcher. Check server logs for details.")


# ============================================================================
# Entry Point
# ============================================================================


def run_server() -> int:
    """Start the MCP server with stdio transport."""
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

    log.info("lgrep_mcp_server_starting", transport="stdio")
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(run_server())
