"""Lifecycle: context, state, and initialization helpers for the lgrep MCP server."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.embeddings import VoyageEmbedder
from lgrep.indexing import Indexer, OperationCancelled
from lgrep.server.runtime import RuntimeSupervisor
from lgrep.storage import (
    BASE_CHECKOUT,
    ChunkStore,
    checkout_scope,
    discover_cached_projects,
    get_project_db_path,
    has_disk_cache,
    write_project_meta,
)
from lgrep.watcher import FileWatcher

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from typing import Any

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

# Foreground budget (seconds) for a worktree search to make its trunk's base
# index current: the staleness check plus at most one base index window.
# Beyond the budget the remaining base work continues as the existing
# background continuation and the search answers from the partial index.
# Keep below LGREP_TOOL_TIMEOUT_S.
DEFAULT_ENSURE_BUDGET_S = 8.0


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
    """State for a single indexed project.

    ``latest_indexed_at`` caches the most-recent chunk timestamp so the
    staleness pre-flight can answer the cheap mtime question without hitting
    LanceDB on every search. ``None`` means "not yet computed"; the pre-flight
    populates it lazily on first call and refreshes it after every full or
    incremental re-index.

    ``pending_index_files`` holds the deterministic remaining work from an
    interrupted bounded index window.  When it is non-empty, the next index
    window resumes from this list instead of re-walking the repository.

    ``base_path`` is set for a linked worktree that shares its trunk's cache
    under worktree dedup: the trunk path whose base rows the worktree's
    overlay is compared against. It is ``None`` for a cache's own checkout.
    """

    db: ChunkStore
    indexer: Indexer
    watcher: FileWatcher | None = None
    watching: bool = False
    latest_indexed_at: float | None = None
    pending_index_files: list[str] | None = None
    base_path: str | None = None


@dataclass
class LgrepContext:
    """Application context supporting multiple concurrent projects.

    Each checkout path gets its own ProjectState (store view, Indexer,
    FileWatcher), keyed by resolved absolute path string. A single
    VoyageEmbedder is shared across all projects to avoid duplicate API
    client overhead.
    ``transport`` records the MCP transport kind (``"stdio"``,
    ``"streamable-http"``, ``"sse"``, ...) when the server is started via
    ``run_server``. It is stored on the application context from an internal
    bootstrap attribute rather than from an environment variable, and is used
    only for diagnostics. ``None`` means "unknown".
    """

    projects: dict[str, ProjectState] = field(default_factory=dict)
    # One owning ChunkStore per cache, keyed by str(canonical_repo_key). Under
    # worktree dedup every worktree of a repo reads a view of its trunk's
    # store, so connections and tables stay bounded by repository count.
    _stores: dict[str, ChunkStore] = field(default_factory=dict)
    # In-flight owner-store assemblies. A cancelled caller detaches while its
    # assembly keeps running here, and any retry for the same owner joins the
    # registered task rather than constructing a second store against the
    # same cache concurrently.
    _store_assemblies: dict[str, asyncio.Task] = field(default_factory=dict)
    # In-flight per-path checkout assemblies (owner store + checkout view +
    # publication), registered on the same terms as owner assemblies.
    _checkout_assemblies: dict[str, asyncio.Task] = field(default_factory=dict)
    embedder: VoyageEmbedder | None = None
    voyage_api_key: str | None = None
    transport: str | None = None
    runtime: RuntimeSupervisor = field(default_factory=RuntimeSupervisor)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _indexing_events: dict[str, asyncio.Event] = field(default_factory=dict)
    _bg_reindex_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    # Release tasks of cancelled index leaders: each holds the project's
    # single-flight event until the leader's physical window has stopped.
    _index_owner_releases: set[asyncio.Task] = field(default_factory=set)
    # Set under the app lock when shutdown starts. A closed context admits
    # no new assemblies or background work and publishes no store or
    # project state, so work that was already running in a worker thread
    # cannot repopulate the context after teardown.
    _closed: bool = False


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

    # Transport is recorded by ``bootstrap.run_server`` in a module attribute.
    # Reading it lazily here avoids a circular import at package load time and
    # keeps the value out of the environment (no side channel).
    from lgrep.server import bootstrap as _bootstrap

    transport = _bootstrap.get_startup_transport()

    ctx = LgrepContext(voyage_api_key=voyage_api_key, transport=transport)
    log.info("lgrep_ready", transport=transport)
    return ctx


async def _shutdown(ctx: LgrepContext) -> None:
    """Gracefully shut down all projects: stop watchers and release resources."""
    log.info("lgrep_shutdown", project_count=len(ctx.projects))
    async with ctx._lock:
        ctx._closed = True

    # Cancel outstanding background reindexes and in-flight checkout and
    # owner-store assemblies; await terminal state so cooperative
    # cancellation propagates through run_blocking and the bounded
    # executor's worker thread reaches a terminal status before we tear
    # down. A cancelled assembly never reaches its publish step, and the
    # closed flag refuses any publication that races teardown.
    tasks = (
        list(ctx._bg_reindex_tasks.values())
        + list(ctx._checkout_assemblies.values())
        + list(ctx._store_assemblies.values())
    )
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        # Abandoned-but-still-running futures update their RuntimeJob record
        # asynchronously via a done callback; give them a bounded window to
        # reach a terminal status before the executor is shut down.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and ctx.runtime.snapshot_active_jobs():
            await asyncio.sleep(0.01)
    # Cancelled index leaders hand their ownership to release tasks that end
    # when the physical window stops; give them the same bounded window.
    releases = list(ctx._index_owner_releases)
    if releases:
        _done, still_running = await asyncio.wait(releases, timeout=2.0)
        for t in still_running:
            t.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)
    ctx._bg_reindex_tasks.clear()
    ctx._store_assemblies.clear()
    ctx._checkout_assemblies.clear()
    ctx._index_owner_releases.clear()

    for proj_path, state in ctx.projects.items():
        _stop_watcher(state, proj_path)

    ctx.projects.clear()
    ctx._stores.clear()
    ctx.runtime.shutdown(cancel_futures=True)
    ctx.embedder = None
    log.info("lgrep_shutdown_complete")


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[LgrepContext]:
    """Manage application lifecycle with optional eager warming."""
    ctx = await _startup(server)
    await _warm_projects(ctx)
    sweep_task = asyncio.create_task(_schedule_startup_sweep(ctx))
    try:
        yield ctx
    finally:
        sweep_task.cancel()
        await _shutdown(ctx)


async def _schedule_startup_sweep(ctx: LgrepContext) -> None:
    """One-shot orphan sweep after a warmup delay.

    Waits 5 minutes for the server to finish warming and initial indexing,
    then runs ``prune_orphans(dry_run=False)`` with all active projects
    passed as the skip set.  The existing grace window (default 1 hour)
    protects caches that were recently written by live indexers.
    """
    try:
        await asyncio.sleep(300)  # 5-minute warmup delay
    except asyncio.CancelledError:
        return  # Server shutting down before sweep

    log.info("startup_orphan_sweep_begin")
    try:
        from lgrep.tools.prune_orphans import prune_orphans as _prune_orphans

        active_set = list(ctx.projects.keys())
        report = await ctx.runtime.run_blocking(
            "startup_orphan_sweep",
            "_schedule_startup_sweep",
            None,
            _prune_orphans,
            dry_run=False,
            active_set=active_set,
            lane="build",
        )
        log.info(
            "startup_orphan_sweep_done",
            deleted=report["deleted_dirs"],
            reclaimed_bytes=report["reclaimed_bytes"],
        )
    except Exception as e:
        log.warning("startup_orphan_sweep_failed", error=str(e))


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


def _assemble_owner_store(owner: Path, owner_str: str) -> ChunkStore:
    """Construct the owner cache's ChunkStore and force its first table touch.

    Pure assembly with no event-loop or app-context access so it can run
    inside a single ``ensure_store`` job on the worker pool: ChunkStore
    construction (LanceDB connect + metadata persistence) and the first
    table touch (``open_table`` + ``count_rows`` + rebuild check + index
    probes) are the synchronous I/O that must never run on the event loop.
    """
    store = ChunkStore(get_project_db_path(owner), project_path=owner_str)
    _ = store.table
    return store


def _assemble_checkout_state(
    store: ChunkStore,
    checkout: str,
    owner_str: str,
    project_path: Path,
    embedder: VoyageEmbedder,
) -> ProjectState:
    """Assemble one checkout's ProjectState view of an owner store.

    Runs inside an ``ensure_checkout`` job on the worker pool:
    ``for_checkout`` reads the overlay-state json, the worktree alias meta
    write takes a blocking ``LOCK_EX`` file lock, Indexer construction
    walks discovery, and the first table touch may yet happen for a
    checkout opened after the owner store published. None of that may run
    on the event loop.
    """
    db = store.for_checkout(checkout)
    base_path = None
    if checkout != BASE_CHECKOUT:
        base_path = owner_str
        write_project_meta(owner_str, db_path=store.db_path, alias_paths=[checkout])
    indexer = Indexer(project_path=project_path, storage=db, embedder=embedder)
    state = ProjectState(db=db, indexer=indexer, base_path=base_path)
    _ = db.table
    return state


async def _assemble_owner_store_task(
    app_ctx: LgrepContext, owner: Path, owner_str: str
) -> ChunkStore:
    """Assemble the owner store, publish it, and clear the registry entry.

    The registry entry is retained until the assembly job is physically
    terminal, so a retry always finds either this entry or the published
    store — never a half-open cache.
    """
    try:
        store = await app_ctx.runtime.run_blocking(
            "ensure_store",
            "_ensure_project_initialized",
            owner_str,
            _assemble_owner_store,
            owner,
            owner_str,
        )
        async with app_ctx._lock:
            # A closed context never gains a store; joiners re-read and
            # receive the shutdown refusal.
            if not app_ctx._closed:
                app_ctx._stores[owner_str] = store
        return store
    finally:
        async with app_ctx._lock:
            if app_ctx._store_assemblies.get(owner_str) is asyncio.current_task():
                app_ctx._store_assemblies.pop(owner_str, None)


async def _join_owner_assembly(task: asyncio.Task) -> None:
    """Wait for an owner assembly without ever cancelling it.

    The await is shielded, so a cancelled waiter detaches promptly — the
    caller's own timeout is never extended by the shared assembly — while
    the assembly keeps running under its registry entry. A retry arriving
    mid-assembly therefore always finds the registry entry or the
    published store, never a half-open cache.
    """
    await asyncio.shield(task)


async def _ensure_owner_store(
    app_ctx: LgrepContext, owner: Path, owner_str: str
) -> ChunkStore | dict:
    """Return the owner cache's store, assembling it at most once.

    Admission and registration are one atomic step under the app lock:
    ``MAX_PROJECTS`` counts published stores plus in-flight assemblies, so
    concurrent distinct owners cannot each pass admission while nothing is
    published yet. Concurrent or retried ensures for the same owner join
    the registered in-flight assembly task instead of constructing a second
    ChunkStore against the same cache.
    """
    while True:
        async with app_ctx._lock:
            if app_ctx._closed:
                return _error_response("lgrep is shutting down.")
            store = app_ctx._stores.get(owner_str)
            if store is not None:
                return store
            task = app_ctx._store_assemblies.get(owner_str)
            if task is None:
                # MAX_PROJECTS counts caches (owners), not worktree views,
                # and counts an in-flight assembly as soon as it reserves.
                count = len(app_ctx._stores) + len(app_ctx._store_assemblies)
                if count >= MAX_PROJECTS:
                    return _error_response(
                        f"Maximum project limit ({MAX_PROJECTS}) reached. "
                        "Restart the server or use the CLI to evict unused projects."
                    )
                if count >= int(MAX_PROJECTS * 0.8):
                    log.warning("approaching_project_limit", current=count, max=MAX_PROJECTS)
                task = asyncio.create_task(
                    _assemble_owner_store_task(app_ctx, owner, owner_str),
                    name=f"ensure_store:{owner_str}",
                )
                app_ctx._store_assemblies[owner_str] = task
        await _join_owner_assembly(task)


async def _ensure_project_initialized(
    app_ctx: LgrepContext, project_path: Path
) -> ProjectState | dict:
    """Look up or create a ProjectState for the given path.

    Uses double-checked locking: fast lock-free path for already-cached projects,
    asyncio.Lock only for first-time initialization.

    Every checkout path gets its own ProjectState, whose Indexer walks that
    checkout. When ``LGREP_WORKTREE_DEDUP`` is enabled, a linked worktree
    shares its trunk's cache: the trunk's ChunkStore is opened once, and the
    worktree's state reads and writes an ``OverlayStore`` view of it.

    Both blocking phases run in the runtime's worker pool, never on the
    event loop: the owner store's assembly (``ensure_store`` job — LanceDB
    connect plus first table touch) is single-flight per cache, and the
    checkout view's assembly (``ensure_checkout`` job — overlay init, alias
    meta write, Indexer construction) is single-flight per path. Each is a
    registered task that owns its work through publication: a cancelled
    caller detaches promptly, and a concurrent or retried caller joins the
    registered task instead of starting a second assembly. The app lock
    stays on the event loop for coordination only.

    Returns ProjectState on success, or a ToolError dict on failure.
    """
    path_key = str(project_path)

    # Fast path: already initialized by this exact path (no lock needed)
    if path_key in app_ctx.projects:
        return app_ctx.projects[path_key]

    async with app_ctx._lock:
        # Double-check after acquiring lock
        if path_key in app_ctx.projects:
            return app_ctx.projects[path_key]

        if not app_ctx.voyage_api_key:
            return _error_response("VOYAGE_API_KEY not set.")
        if app_ctx._closed:
            return _error_response("lgrep is shutting down.")

        task = app_ctx._checkout_assemblies.get(path_key)
        if task is None:
            task = asyncio.create_task(
                _assemble_project_state_task(app_ctx, project_path, path_key),
                name=f"ensure_checkout:{path_key}",
            )
            app_ctx._checkout_assemblies[path_key] = task

    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if task.cancelled() and not (current is not None and current.cancelling()):
            # Shutdown cancelled the shared assembly; this caller was not
            # itself cancelled.
            return _error_response("lgrep is shutting down.")
        raise


async def _assemble_project_state_task(
    app_ctx: LgrepContext, project_path: Path, path_key: str
) -> ProjectState | dict:
    """Assemble and publish one path's ProjectState, then clear its registry entry.

    The registry entry is retained until the assembly is physically
    terminal, so a retry always finds either this entry or the published
    state, and never starts a duplicate checkout assembly.
    """
    # The cache owner is the trunk under dedup and the path itself
    # otherwise; checkout is BASE_CHECKOUT unless the path is a linked
    # worktree of the owner.
    owner, checkout = checkout_scope(project_path)
    owner_str = str(owner)
    try:
        # Create shared embedder on first use
        if app_ctx.embedder is None:
            app_ctx.embedder = VoyageEmbedder(api_key=app_ctx.voyage_api_key)

        store = await _ensure_owner_store(app_ctx, owner, owner_str)
        if isinstance(store, dict):
            return store

        state = await app_ctx.runtime.run_blocking(
            "ensure_checkout",
            "_ensure_project_initialized",
            owner_str,
            _assemble_checkout_state,
            store,
            checkout,
            owner_str,
            project_path,
            app_ctx.embedder,
        )
        async with app_ctx._lock:
            if app_ctx._closed:
                return _error_response("lgrep is shutting down.")
            existing = app_ctx.projects.get(path_key)
            if existing is not None:
                return existing
            app_ctx.projects[path_key] = state
        log.info(
            "project_initialized",
            project=path_key,
            cache_owner=owner_str,
            overlay=state.base_path is not None,
        )
        return state
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("initialization_failed", project=path_key, error=str(e))
        return _error_response("Failed to initialize project.")
    finally:
        async with app_ctx._lock:
            if app_ctx._checkout_assemblies.get(path_key) is asyncio.current_task():
                app_ctx._checkout_assemblies.pop(path_key, None)


async def _run_blocking_or_thread(
    runtime: RuntimeSupervisor | None,
    kind: str,
    caller: str,
    project: str,
    fn: Callable[[], Any],
) -> Any:
    if runtime is not None:
        return await runtime.run_blocking(kind, caller, project, fn)
    return await asyncio.to_thread(fn)


async def _get_project_stats(
    proj_path: str, state: ProjectState, runtime: RuntimeSupervisor | None = None
) -> dict:
    """Get stats for a single project. Safe to call concurrently via asyncio.gather.

    Always returns a dict matching the ``StatusSemanticResult`` TypedDict shape
    (including ``disk_cache`` and ``error`` keys), so callers can pass the dict
    directly into the typed result without missing-key validation errors.
    """
    try:
        chunks = await _run_blocking_or_thread(
            runtime,
            "status_count_chunks",
            "_get_project_stats",
            proj_path,
            state.db.count_chunks,
        )
        files_set = await _run_blocking_or_thread(
            runtime,
            "status_indexed_files",
            "_get_project_stats",
            proj_path,
            state.db.get_indexed_files,
        )
        return {
            "files": len(files_set),
            "chunks": chunks,
            "watching": state.watching,
            "project": proj_path,
            "disk_cache": None,
            "error": None,
        }
    except Exception as e:
        log.exception("status_failed", project=proj_path, error=str(e))
        return {
            "files": 0,
            "chunks": 0,
            "watching": False,
            "project": proj_path,
            "disk_cache": None,
            "error": str(e),
        }


def _check_staleness(state: ProjectState) -> tuple[bool, int]:
    """Cheap three-stage freshness check.

    Returns ``(stale, suspect_count)``. ``stale=True`` means the on-disk file
    set has drifted from the index and a re-embed is needed before search.

    Stages, ordered cheapest first so the warm path (no edits since last
    index) costs only a directory walk + ``stat`` per file:

    1. mtime gate — collect current files with their ``stat().st_mtime``.
       Deleted indexed files are stale. Otherwise any file whose mtime exceeds
       the cached ``latest_indexed_at`` becomes a suspect. We intentionally do
       not compare file counts because discovered files can legitimately
       produce zero chunks (empty/comment-only files) and therefore never
       appear in the indexed file set.
    2. hash check — only suspect files are read+hashed and compared against
       the stored ``file_hash`` from ``ChunkStore.get_file_hashes()``. A
       single batched projection query handles all comparisons.
    3. caller acts on the result. Any errors during the check are treated
       as "fresh" so transient I/O issues never cause an unintended re-embed.

    The whole check is bounded by ``LGREP_STALENESS_DEADLINE_S`` (default
    4.0s) so a large repo's directory walk cannot eat the entire 8s tool
    timeout. On deadline, the function returns ``(False, 0)`` and the
    search proceeds with the slightly-stale index; the next search will
    trigger a fresh reindex if drift is real.
    """
    deadline_s = float(os.environ.get("LGREP_STALENESS_DEADLINE_S", "4.0"))
    deadline = time.monotonic() + deadline_s

    def _over_deadline() -> bool:
        return time.monotonic() > deadline

    try:
        # A worktree overlay whose base changed since its last full
        # comparison must re-hash every file: base writes leave worktree
        # mtimes untouched.
        if state.base_path is not None and state.db.needs_full_recheck():
            return True, 0

        indexer = state.indexer
        if state.latest_indexed_at is None:
            state.latest_indexed_at = state.db.get_latest_indexed_at()
        latest = state.latest_indexed_at or 0.0

        indexed_files = set(state.db.get_indexed_files() or set())
        current: list[tuple[Path, float, str]] = []
        current_rel_paths: set[str] = set()
        project_root = Path(indexer.project_path)
        for fp in indexer.discovery.find_files():
            if _over_deadline():
                log.warning(
                    "staleness_check_deadline_exceeded",
                    project=str(indexer.project_path),
                    deadline_s=deadline_s,
                )
                return False, 0
            try:
                rel = str(fp.relative_to(project_root))
                current.append((fp, fp.stat().st_mtime, rel))
                current_rel_paths.add(rel)
            except (OSError, ValueError):
                continue

        # Stage 0 — current files that are absent from the indexed set.  This
        # catches partial indexes where a bounded window left files pending,
        # even though their mtime is older than the latest chunk timestamp,
        # and a worktree overlay whose every file differs from base.
        # Exclude zero-chunk files, which are intentionally not represented in
        # the chunks table but are tracked separately.
        try:
            zero_chunk_files = set(state.db.get_zero_chunk_files())
        except Exception:
            zero_chunk_files = set()
        never_indexed = current_rel_paths - indexed_files - zero_chunk_files
        if never_indexed:
            log.info(
                "staleness_never_indexed_files",
                project=str(indexer.project_path),
                count=len(never_indexed),
            )
            return True, len(never_indexed)

        # Stage 1a — indexed files that no longer exist on disk are stale.
        deleted_indexed_files = indexed_files - current_rel_paths
        if deleted_indexed_files:
            return True, len(deleted_indexed_files)

        # Stage 1b — pick files whose mtime is newer than the index timestamp.
        suspects = [(fp, rel) for fp, mt, rel in current if mt > latest]
        if not suspects:
            return False, 0

        # Stage 2 — hash only the suspect subset.
        stored = state.db.get_file_hashes()
        for fp, rel in suspects:
            if _over_deadline():
                log.warning(
                    "staleness_check_deadline_exceeded",
                    project=str(indexer.project_path),
                    deadline_s=deadline_s,
                )
                return False, 0
            try:
                content = fp.read_bytes()
            except OSError:
                continue
            current_hash = hashlib.sha256(content).hexdigest()
            if stored.get(rel) != current_hash:
                return True, len(suspects)

        # All suspects had matching hashes (mtime touched but content unchanged).
        return False, 0
    except Exception as e:  # pragma: no cover — pre-flight must never crash search
        log.debug("staleness_check_failed", error=str(e))
        return False, 0


def _index_in_flight(app_ctx: LgrepContext, project_path: str) -> bool:
    return project_path in app_ctx._indexing_events or project_path in app_ctx._bg_reindex_tasks


async def _base_index_ready(app_ctx: LgrepContext, base_path: str, wait: bool) -> bool:
    """Bring a trunk's base rows current before a worktree compares against them.

    A worktree overlay holds only files that differ from base, so an
    overlay computed against a stale or partial base embeds files the base
    would have covered. When the base is stale, a background pass waits for
    the base reindex; a foreground pass runs the staleness check plus at
    most one base window, bounded by ``LGREP_ENSURE_BUDGET_S`` (default
    8.0). Beyond the budget, or when the window does not converge, the
    remaining base work continues as the existing background continuation
    and the worktree search answers from the current partial index (the
    staleness pre-flight keeps surfacing freshness). Returns True when the
    base has no pending work, or when the base cannot be indexed (the
    overlay then covers every differing file).
    """
    base = await _ensure_project_initialized(app_ctx, Path(base_path))
    if isinstance(base, dict):
        log.warning("overlay_base_unavailable", base=base_path, error=base.get("error"))
        return True
    if not _index_in_flight(app_ctx, base_path):
        if wait:
            stale, _ = await app_ctx.runtime.run_blocking(
                "staleness_check", "_base_index_ready", base_path, _check_staleness, base
            )
            if not stale and not base.pending_index_files:
                return True
            await _schedule_background_reindex(app_ctx, base_path, Path(base_path))
        else:
            return await _run_budgeted_base_window(app_ctx, base_path, base)
    if not wait:
        return False
    while _index_in_flight(app_ctx, base_path):
        event = app_ctx._indexing_events.get(base_path)
        task = app_ctx._bg_reindex_tasks.get(base_path)
        if event is not None:
            await event.wait()
        elif task is not None:
            await asyncio.wait({task})
        # Let the finished task's done callback unregister it.
        await asyncio.sleep(0)
    return not base.pending_index_files


async def _run_budgeted_base_window(
    app_ctx: LgrepContext, base_path: str, base: ProjectState
) -> bool:
    """Make the base current inside ``LGREP_ENSURE_BUDGET_S``, then defer.

    The budget covers the whole foreground pass — the staleness check
    (including its queue wait) plus the wait for at most one base window —
    so neither phase can consume the caller's tool timeout on its own.

    The base window never runs as part of the caller: it runs as the
    registered single-flight background reindex, and the caller only waits
    for it. When the budget runs out, the caller stops waiting and the
    worktree search answers from the partial index; the window keeps its
    single-flight ownership until it physically finishes and then hands any
    remaining files to the existing continuation. Cancelling the window at
    the deadline would release ownership while its storage writes were
    still running, and a second window could then interleave with them.
    """
    budget_s = float(os.environ.get("LGREP_ENSURE_BUDGET_S", DEFAULT_ENSURE_BUDGET_S))
    deadline = time.monotonic() + budget_s
    try:
        stale, _ = await asyncio.wait_for(
            app_ctx.runtime.run_blocking(
                "staleness_check", "_base_index_ready", base_path, _check_staleness, base
            ),
            timeout=max(0.0, deadline - time.monotonic()),
        )
    except TimeoutError:
        log.info("base_window_budget_exceeded", base=base_path, budget_s=budget_s)
        await _schedule_background_reindex(app_ctx, base_path, Path(base_path))
        return False
    if not stale and not base.pending_index_files:
        return True

    await _schedule_background_reindex(app_ctx, base_path, Path(base_path))
    owner = app_ctx._bg_reindex_tasks.get(base_path)
    event = app_ctx._indexing_events.get(base_path)
    try:
        remaining = max(0.0, deadline - time.monotonic())
        if owner is not None:
            await asyncio.wait_for(asyncio.shield(owner), timeout=remaining)
        elif event is not None:
            await asyncio.wait_for(event.wait(), timeout=remaining)
    except TimeoutError:
        log.info("base_window_budget_exceeded", base=base_path, budget_s=budget_s)
        return False
    # Let the finished owner's done callback unregister it.
    await asyncio.sleep(0)
    return not _index_in_flight(app_ctx, base_path) and not base.pending_index_files


async def _finish_single_flight_indexing(
    app_ctx: LgrepContext, project_path: str, event: asyncio.Event
) -> None:
    """Release single-flight state and notify any waiting followers."""
    async with app_ctx._lock:
        if app_ctx._indexing_events.get(project_path) is event:
            app_ctx._indexing_events.pop(project_path, None)
    event.set()


async def _schedule_background_reindex(
    app_ctx: LgrepContext, project_path: str, path_obj: Path
) -> None:
    """Trigger a non-blocking single-flight reindex; never raises.

    Cheap: acquires the lock, checks for an in-flight reindex, creates the
    background task, returns. The reindex itself runs detached.
    Dedupes via ``app_ctx._indexing_events``: if a reindex is already in
    flight for this project, no-op. The task is tracked in
    ``app_ctx._bg_reindex_tasks`` to prevent GC and to allow shutdown
    cancellation.
    """
    async with app_ctx._lock:
        # The event is installed by the background task after it starts.  Use
        # the task registry too: concurrent scheduler calls can otherwise all
        # run before the first task gets its initial time slice, producing
        # untracked follower tasks and leaving the leader uncancellable during
        # shutdown.
        if app_ctx._closed:
            return
        if project_path in app_ctx._indexing_events or project_path in app_ctx._bg_reindex_tasks:
            return  # already in flight
        task = asyncio.create_task(
            _auto_index_project_single_flight(app_ctx, project_path, path_obj, wait_for_base=True),
            name=f"bg_reindex:{project_path}",
        )
        app_ctx._bg_reindex_tasks[project_path] = task
    task.add_done_callback(lambda t, p=project_path: _on_bg_reindex_done(app_ctx, p, t))


async def _run_index_continuation(app_ctx: LgrepContext, project_path: str, path_obj: Path) -> None:
    """Schedule a background continuation that processes remaining pending files.

    Called by an index window that did not complete.  It installs a fresh
    background task that resumes from ``state.pending_index_files`` and loops
    until convergence.  The caller is responsible for releasing the
    single-flight event before calling this helper so searches do not block on
    the continuation.
    """
    async with app_ctx._lock:
        if app_ctx._closed:
            return
        # Overwriting the caller's own task entry is intentional: the caller's
        # done callback will see a different task object and no-op.
        task = asyncio.create_task(
            _auto_index_project_single_flight(
                app_ctx, project_path, path_obj, continue_until_complete=True, wait_for_base=True
            ),
            name=f"bg_reindex_continuation:{project_path}",
        )
        app_ctx._bg_reindex_tasks[project_path] = task
    task.add_done_callback(lambda t, p=project_path: _on_bg_reindex_done(app_ctx, p, t))


def _on_bg_reindex_done(app_ctx: LgrepContext, project_path: str, task: asyncio.Task) -> None:
    """Remove a finished background reindex task from the registry and log.

    Never raises. Only removes the registry entry if it still points to the
    same task object, so a superseded task cannot wipe a newer one.
    """
    if app_ctx._bg_reindex_tasks.get(project_path) is not task:
        return
    if task.cancelled():
        log.info("bg_reindex_cancelled", project=project_path)
    else:
        exc = task.exception()
        if exc is not None:
            log.error("bg_reindex_failed", project=project_path, error=str(exc))
        else:
            result = task.result()
            if isinstance(result, dict) and "error" in result:
                log.error("bg_reindex_failed", project=project_path, error=result.get("error"))
            else:
                log.info("bg_reindex_success", project=project_path)
    app_ctx._bg_reindex_tasks.pop(project_path, None)


async def _auto_index_project_single_flight(
    app_ctx: LgrepContext,
    project_path: str,
    path_obj: Path,
    continue_until_complete: bool = False,
    wait_for_base: bool = False,
) -> ProjectState | dict:
    """Auto-index project on first search using leader/follower coordination.

    Processes one bounded index window by default.  If the window does not
    converge, it stores the remaining pending files on ``state`` and schedules
    a background continuation, then returns the state so the calling search
    does not block.  When ``continue_until_complete`` is true, the coroutine
    loops windows in the background until the pending set is empty.

    A worktree overlay is compared against its trunk's base rows, so the
    base is brought current first (``_base_index_ready``). If the base is
    still indexing, the overlay pass is skipped and the state returned;
    ``wait_for_base`` makes background passes wait for the base instead.
    """
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

    log.info(
        "search_auto_index_start",
        project=project_path,
        continue_until_complete=continue_until_complete,
    )
    ownership_handed_off = False
    try:
        result = await _ensure_project_initialized(app_ctx, path_obj)
        if isinstance(result, dict):
            return result
        state = result

        if state.base_path is not None and not await _base_index_ready(
            app_ctx, state.base_path, wait=wait_for_base
        ):
            log.info("overlay_index_deferred_for_base", project=project_path, base=state.base_path)
            return state

        # Seed pending work so cancellation can preserve it even if the
        # blocking index_window raises before returning a window result.
        if state.pending_index_files is None:
            try:
                state.pending_index_files = await app_ctx.runtime.run_blocking(
                    "compute_pending_files",
                    "_auto_index_project_single_flight",
                    project_path,
                    state.indexer.compute_pending_files,
                    lane="build",
                )
            except Exception as e:
                log.warning("compute_pending_files_failed", project=project_path, error=str(e))

        while True:
            pending = state.pending_index_files
            window = None
            for attempt in range(1, AUTO_INDEX_MAX_ATTEMPTS + 1):
                # Construct a fresh cancel_event per window attempt so the
                # supervisor can propagate the awaiting asyncio coroutine's
                # cancellation to the blocking index_window work. The indexer
                # checks the event at file boundaries and raises
                # OperationCancelled, allowing the bounded executor worker
                # thread to exit instead of holding the slot forever.
                cancel_event = threading.Event()
                window_job = asyncio.ensure_future(
                    app_ctx.runtime.run_blocking(
                        "index_window",
                        "_auto_index_project_single_flight",
                        project_path,
                        lambda cancel_event=cancel_event, pending=pending: (
                            state.indexer.index_window(
                                cancel_event=cancel_event, pending_files=pending
                            )
                        ),
                        cancel_event=cancel_event,
                        lane="build",
                    )
                )
                try:
                    window = await asyncio.shield(window_job)
                    break
                except asyncio.CancelledError:
                    # The caller detaches now. The physical window keeps the
                    # single-flight ownership until it stops at its next
                    # file boundary, so no second window can interleave
                    # with its storage writes.
                    cancel_event.set()
                    state.pending_index_files = pending
                    ownership_handed_off = True
                    _retain_ownership_until_stopped(app_ctx, project_path, event, window_job)
                    raise
                except OperationCancelled:
                    # Preserve whatever pending set we had before cancellation
                    # so a future continuation can resume.
                    state.pending_index_files = pending
                    log.info(
                        "search_auto_index_cancelled",
                        project=project_path,
                        attempt=attempt,
                        max_attempts=AUTO_INDEX_MAX_ATTEMPTS,
                        remaining_files=len(pending) if pending else 0,
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

            # Update remaining work and refresh the cached freshness timestamp
            # at every window boundary so partial indexes are visible to the
            # staleness pre-flight.
            state.pending_index_files = window.remaining_files
            try:
                state.latest_indexed_at = await app_ctx.runtime.run_blocking(
                    "db_latest_indexed_at",
                    "_auto_index_project_single_flight",
                    project_path,
                    state.db.get_latest_indexed_at,
                )
            except Exception as e:
                log.debug("latest_indexed_at_refresh_failed", error=str(e))

            if window.complete:
                state.pending_index_files = None
                log.info(
                    "search_auto_index_success",
                    project=project_path,
                    files=window.status.file_count,
                    chunks=window.status.chunk_count,
                    duration_ms=round(window.status.duration_ms, 2),
                )
                return state

            if not continue_until_complete:
                # One window done; hand remaining work to a background
                # continuation and return state immediately so search can
                # proceed with the partial index.
                await _finish_single_flight_indexing(app_ctx, project_path, event)
                await _run_index_continuation(app_ctx, project_path, path_obj)
                log.info(
                    "search_auto_index_continuation_scheduled",
                    project=project_path,
                    remaining_files=len(window.remaining_files),
                )
                return state

            # Continuation mode: yield briefly so the event loop can service
            # other work between bounded windows.
            await asyncio.sleep(0)
    except Exception as e:
        app_ctx.projects.pop(project_path, None)
        log.exception("search_auto_index_failed", project=project_path, error=str(e))
        return _error_response(
            "Failed to auto-index project on first search. Check server logs for details."
        )
    finally:
        if not ownership_handed_off:
            await _finish_single_flight_indexing(app_ctx, project_path, event)


def _retain_ownership_until_stopped(
    app_ctx: LgrepContext, project_path: str, event: asyncio.Event, window_job: asyncio.Future
) -> None:
    """Release a cancelled leader's single-flight ownership only after its window stops.

    The release task is tracked so shutdown can reconcile it.
    """

    async def _release() -> None:
        try:
            await asyncio.wait({window_job})
            if not window_job.cancelled():
                window_job.exception()  # retrieved; the leader already logged its cancel
        finally:
            await _finish_single_flight_indexing(app_ctx, project_path, event)

    task = asyncio.create_task(_release(), name=f"index_owner_release:{project_path}")
    app_ctx._index_owner_releases.add(task)
    task.add_done_callback(app_ctx._index_owner_releases.discard)


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
            return {
                "path": path_str,
                "status": "skipped",
                "detail": result.get("error", str(result)),
            }

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
