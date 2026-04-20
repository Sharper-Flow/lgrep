"""Lifecycle: context, state, and initialization helpers for the lgrep MCP server."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.embeddings import VoyageEmbedder
from lgrep.indexing import Indexer
from lgrep.storage import (
    ChunkStore,
    discover_cached_projects,
    get_project_db_path,
    has_disk_cache,
)
from lgrep.watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server.fastmcp import FastMCP

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants (imported from parent after package init)
# ---------------------------------------------------------------------------
# These are set by __init__.py before this module is loaded, so we can
# reference them via late binding.  We import them here for use in
# lifecycle functions; the parent defines the canonical values.
# ---------------------------------------------------------------------------

MAX_PROJECTS: int = 20  # overridden by __init__.py import
AUTO_INDEX_MAX_ATTEMPTS: int = 2  # overridden by __init__.py import
AUTO_INDEX_RETRY_BASE_DELAY_S: float = 0.1  # overridden by __init__.py import


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _error_response(message: str) -> dict:
    """Create a structured error response dict (ToolError shape)."""
    return {"error": message}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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

    ``transport`` records the MCP transport kind (``"stdio"``, ``"http"``,
    ``"sse"``, ...) when the server is started via ``run_server``. Tools
    that perform destructive operations use this to apply transport-aware
    safety (for example, refusing ``dry_run=False`` on shared HTTP
    transports). ``None`` means "unknown" and is treated as untrusted.
    """

    projects: dict[str, ProjectState] = field(default_factory=dict)
    embedder: VoyageEmbedder | None = None
    voyage_api_key: str | None = None
    transport: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _indexing_events: dict[str, asyncio.Event] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------


async def _startup(server: FastMCP) -> LgrepContext:
    """Initialize application context and validate environment.

    Returns a fully configured LgrepContext ready for tool calls.
    """
    log.info("lgrep_starting", server=server.name)

    voyage_api_key = os.environ.get("VOYAGE_API_KEY")
    if not voyage_api_key:
        log.error("voyage_api_key_missing", hint="Set VOYAGE_API_KEY env var")

    # Transport is populated by ``bootstrap.run_server`` via
    # LGREP_TRANSPORT; absent when running under tests or embedded use
    # where the caller did not go through run_server.
    transport = os.environ.get("LGREP_TRANSPORT")

    ctx = LgrepContext(voyage_api_key=voyage_api_key, transport=transport)
    log.info("lgrep_ready", transport=transport)
    return ctx


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
) -> ProjectState | dict:
    """Look up or create a ProjectState for the given path.

    Uses double-checked locking: fast lock-free path for already-cached projects,
    asyncio.Lock only for first-time initialization.

    Returns ProjectState on success, or a ToolError dict on failure.
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
            db = ChunkStore(db_path, project_path=path_key)
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
) -> ProjectState | dict:
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
        if isinstance(result, dict):
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


async def _ensure_search_project_state(app_ctx: LgrepContext, path: str) -> ProjectState | dict:
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


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------


async def _warm_project(app_ctx: LgrepContext, project_path: Path) -> dict:
    """Warm a single project by loading its disk cache into memory.

    Isolated error handling — never raises.  Returns a status dict
    so the caller can log a summary.
    """
    path_str = str(project_path)
    try:
        result = await _ensure_project_initialized(app_ctx, project_path)
        if isinstance(result, dict):
            log.warning("warm_skipped", project=path_str, reason=result.get("error", str(result)))
            return {"path": path_str, "status": "skipped", "detail": result.get("error", str(result))}

        # Start watcher if auto-watch is enabled
        auto_watch = os.environ.get("LGREP_AUTO_WATCH", "").lower() in ("true", "1", "yes")
        if auto_watch and not result.watching:
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
    """Eagerly load cached indexes at startup.

    Checks two sources in order:

    1. ``LGREP_WARM_PATHS`` env var — explicit ``os.pathsep``-separated
       list of project directories.
    2. Auto-discover from disk — scans ``~/.cache/lgrep/*/project_meta.json``
       for projects that were previously indexed, sorted by most recently
       used.  Controlled by ``LGREP_AUTO_WARM_DISK`` env var (default: true).

    Errors in individual projects are logged and skipped; warming never
    blocks server startup.
    """
    raw = os.environ.get("LGREP_WARM_PATHS", "")

    paths: list[Path] = []
    if raw:
        # Parse, expand, resolve, deduplicate
        seen: set[str] = set()
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
        log.info("warm_source", source="env", candidates=len(paths))
    else:
        # Auto-discover from disk caches that have project_meta.json
        auto_warm = os.environ.get("LGREP_AUTO_WARM_DISK", "true").lower()
        if auto_warm in ("true", "1", "yes"):
            discovered = discover_cached_projects(max_results=MAX_PROJECTS)
            paths = [Path(p) for p in discovered]
            if paths:
                log.info("warm_source", source="disk_discovery", candidates=len(paths))

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
