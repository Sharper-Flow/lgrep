"""lgrep MCP server package facade."""

from __future__ import annotations

import asyncio
import functools
import os
import time
from importlib import import_module
from pathlib import Path

import structlog
from mcp.server.fastmcp import FastMCP

log = structlog.get_logger()

# Guard against unbounded memory growth.
MAX_PROJECTS = 20
AUTO_INDEX_MAX_ATTEMPTS = 2
AUTO_INDEX_RETRY_BASE_DELAY_S = 0.1

# Server-side timeout for tool operations.
TOOL_TIMEOUT_S = float(os.environ.get("LGREP_TOOL_TIMEOUT_S", "45"))


def time_tool(func):
    """Decorator to time tool execution, log results, and enforce timeout."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        tool_name = func.__name__
        try:
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=TOOL_TIMEOUT_S)
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.info(f"{tool_name}_completed", duration_ms=duration)
            return result
        except TimeoutError:
            duration = round((time.perf_counter() - start) * 1000, 2)
            message = (
                f"Operation timed out after {TOOL_TIMEOUT_S}s. "
                "The project may need re-indexing or the Voyage API may be slow. "
                "Try again or use a non-semantic search tool."
            )
            log.error(
                f"{tool_name}_timeout",
                duration_ms=duration,
                timeout_s=TOOL_TIMEOUT_S,
            )
            if tool_name == "search_text":
                return {
                    "results": [],
                    "max_results": kwargs.get("max_results", 50),
                    "_meta": {"duration_ms": duration, "tool": tool_name},
                    "error": message,
                }

            from lgrep.server.responses import error_response as _err

            return _err(message)
        except Exception as e:
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.exception(f"{tool_name}_failed", duration_ms=duration, error=str(e))
            raise

    return wrapper


from lgrep.server import lifecycle as _lifecycle  # noqa: E402

_lifecycle.MAX_PROJECTS = MAX_PROJECTS
_lifecycle.AUTO_INDEX_MAX_ATTEMPTS = AUTO_INDEX_MAX_ATTEMPTS
_lifecycle.AUTO_INDEX_RETRY_BASE_DELAY_S = AUTO_INDEX_RETRY_BASE_DELAY_S

from lgrep.server.lifecycle import (  # noqa: E402
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
    _ensure_search_project_state,
    _get_project_stats,
    _shutdown,
    _startup,
    _stop_watcher,
    _warm_project,
    _warm_projects,
    app_lifespan,
)

mcp = FastMCP(
    "lgrep",
    lifespan=app_lifespan,
    stateless_http=True,
)

# Import tool modules for decorator side effects after mcp exists.
import_module("lgrep.server.tools_semantic")
import_module("lgrep.server.tools_symbols")
import_module("lgrep.server.tools_maintenance")
import_module("lgrep.server.tools_diagnostics")

from lgrep.server.bootstrap import run_server  # noqa: E402
from lgrep.server.tools_diagnostics import lgrep_diagnostics  # noqa: E402
from lgrep.server.tools_maintenance import (  # noqa: E402
    invalidate_worktree_cache,
    prune_orphans,
    prune_symbols,
)
from lgrep.server.tools_semantic import (  # noqa: E402
    index_semantic,
    search_semantic,
    status_semantic,
    watch_start_semantic,
    watch_stop_semantic,
)
from lgrep.server.tools_symbols import (  # noqa: E402
    get_file_outline,
    get_file_tree,
    get_repo_outline,
    get_symbol,
    get_symbols,
    index_symbols_folder,
    index_symbols_repo,
    invalidate_cache,
    list_repos,
    search_symbols,
    search_text,
)


def remove_project(app_ctx: LgrepContext, path: str) -> dict:
    """Remove project from server memory, freeing its resource slot.

    When worktree dedup is enabled, ProjectState may be shared across multiple
    paths. We only stop the watcher and drop the canonical entry when the last
    aliased path is removed. Removing a single alias just unmaps that path.
    """
    log.info("lgrep_remove", project=path)

    project_path = str(Path(path).resolve())
    state = app_ctx.projects.get(project_path)
    if not state:
        return {"removed": False, "message": "Project not loaded", "project": project_path}

    # Find all paths that point to this same state (alias detection)
    aliased_paths = [p for p, s in app_ctx.projects.items() if s is state]
    is_last_reference = len(aliased_paths) == 1

    if is_last_reference:
        # Last reference — tear down completely
        _stop_watcher(state, project_path)
        # Remove the canonical entry too
        canonical_key = None
        for ck, st in app_ctx._canonical_to_state.items():
            if st is state:
                canonical_key = ck
                break
        if canonical_key is not None:
            del app_ctx._canonical_to_state[canonical_key]

    del app_ctx.projects[project_path]
    log.info(
        "project_removed",
        project=project_path,
        remaining=len(app_ctx.projects),
        was_last_reference=is_last_reference,
    )

    return {
        "removed": True,
        "project": project_path,
        "remaining_projects": len(app_ctx.projects),
    }


__all__ = [
    "mcp",
    "log",
    "time_tool",
    "LgrepContext",
    "ProjectState",
    "MAX_PROJECTS",
    "AUTO_INDEX_MAX_ATTEMPTS",
    "AUTO_INDEX_RETRY_BASE_DELAY_S",
    "TOOL_TIMEOUT_S",
    "_startup",
    "_shutdown",
    "_ensure_project_initialized",
    "_ensure_search_project_state",
    "_get_project_stats",
    "_stop_watcher",
    "_warm_project",
    "_warm_projects",
    "app_lifespan",
    "run_server",
    "remove_project",
    "lgrep_diagnostics",
    "search_semantic",
    "index_semantic",
    "status_semantic",
    "watch_start_semantic",
    "watch_stop_semantic",
    "index_symbols_folder",
    "index_symbols_repo",
    "list_repos",
    "get_file_tree",
    "get_file_outline",
    "get_repo_outline",
    "search_symbols",
    "search_text",
    "get_symbol",
    "get_symbols",
    "invalidate_cache",
    "prune_orphans",
    "prune_symbols",
    "invalidate_worktree_cache",
]
