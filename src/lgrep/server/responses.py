"""MCP response contracts for lgrep tools.

This module defines the canonical TypedDict shapes for all MCP tool responses.
TypedDicts are used for maintainer-side type safety — FastMCP itself accepts
plain dicts at runtime, so these are checked by type checkers but do not
enforce validation at runtime.

**Location:** ``lgrep.server.responses`` (moved from ``lgrep.server_responses``
during the server split).

Response convention:
  - Successful responses use their specific TypedDict.
  - Errors always return ``ToolError`` via ``error_response()``.
  - Internal helpers (``_get_project_stats``) return plain dicts; the
    wrapping tool handler formats them into the appropriate TypedDict before
    returning.
"""

from __future__ import annotations

import asyncio
import functools
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypedDict, TypeVar

import structlog

log: structlog.BoundLogger = structlog.get_logger("lgrep.server")


# --------------------------------------------------------------------------- #
# Constants (mirrored from server.py — canonical location)
# --------------------------------------------------------------------------- #

TOOL_TIMEOUT_S = float(os.environ.get("LGREP_TOOL_TIMEOUT_S", "45"))


# --------------------------------------------------------------------------- #
# TypedDict definitions
# --------------------------------------------------------------------------- #


class ToolError(TypedDict):
    """Error response returned by any tool on failure."""

    error: str


class SearchSemanticResult(TypedDict):
    """Response for search_semantic."""

    results: list[SearchChunk]
    total: int
    query: str
    path: str
    engine: str


class SearchChunk(TypedDict):
    file_path: str
    line_number: int
    content: str
    score: float


class IndexSemanticResult(TypedDict):
    """Response for index_semantic."""

    file_count: int
    chunk_count: int
    duration_ms: float
    total_tokens: int


class StatusSemanticResult(TypedDict):
    """Response for status_semantic (single-project or all-projects)."""

    files: int
    chunks: int
    watching: bool
    project: str
    disk_cache: bool | None  # None = not applicable / no disk cache read
    error: str | None  # present only on error


class StatusAllProjectsResult(TypedDict):
    """Response when status_semantic is called with no path (all projects)."""

    projects: list[StatusSemanticResult]


class WatchStartResult(TypedDict):
    """Response for watch_start_semantic."""

    path: str
    watching: bool
    message: str | None  # "Already watching" when idempotent


class WatchStopResult(TypedDict):
    """Response for watch_stop_semantic (single project)."""

    stopped: bool
    project: str | None  # None when stopping all
    message: str | None  # "Not watching" when no active watcher


class WatchStopAllResult(TypedDict):
    """Response for watch_stop_semantic (all projects)."""

    stopped: bool
    projects_stopped: list[str]


class IndexSymbolsFolderResult(TypedDict):
    """Response for index_symbols_folder."""

    files_indexed: int
    files_skipped: int
    symbols_indexed: int
    repo_path: str
    _meta: _Meta


class IndexSymbolsRepoResult(TypedDict):
    """Response for index_symbols_repo."""

    files_indexed: int
    symbols_indexed: int
    repo: str
    _meta: _Meta


class ListReposResult(TypedDict):
    """Response for list_repos."""

    repos: list[str]
    _meta: _Meta


class GetFileTreeResult(TypedDict):
    """Response for get_file_tree."""

    files: list[str]
    total_files: int
    _meta: _Meta


class GetFileOutlineResult(TypedDict):
    """Response for get_file_outline."""

    file_path: str
    symbols: list[Any]
    symbol_count: int
    _meta: _Meta


class GetRepoOutlineResult(TypedDict):
    """Response for get_repo_outline."""

    repo_path: str
    files: list[str]
    total_files: int
    total_symbols: int
    _meta: _Meta


class SearchSymbolsResult(TypedDict):
    """Response for search_symbols."""

    results: list[Any]
    total_matches: int
    _meta: _Meta


class SearchTextResult(TypedDict):
    """Response for search_text."""

    results: list[SearchTextMatch]
    max_results: int
    _meta: _Meta


class SearchTextMatch(TypedDict):
    file_path: str
    line_number: int
    line: str


class GetSymbolResult(TypedDict):
    """Response for get_symbol."""

    symbol: dict[str, Any]
    _meta: _Meta


class GetSymbolsResult(TypedDict):
    """Response for get_symbols."""

    symbols: list[dict[str, Any]]
    _meta: _Meta


class InvalidateCacheResult(TypedDict):
    """Response for invalidate_cache."""

    status: str  # "deleted" | "not_found"
    _meta: _Meta


class _Meta(TypedDict):
    """Envelope metadata attached to symbol-tool responses."""

    duration_ms: float
    tool: str


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #

def error_response(message: str) -> ToolError:
    """Create a structured error response.

    This replaces the legacy ``_error_response()`` which returned a
    ``json.dumps({"error": message})`` string.
    """
    return ToolError(error=message)


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def time_tool(func: F) -> F:
    """Decorator to time tool execution, log results, and enforce a server-side timeout.

    Wraps tool calls in asyncio.wait_for() so the server returns a structured
    error before the MCP client's transport-level timeout fires.

    The wrapped coroutine may return either a TypedDict response or a
    ``ToolError`` (from ``error_response()``). This decorator does not
    convert types — it only handles timeout injection.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        tool_name = func.__name__
        try:
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=TOOL_TIMEOUT_S)
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.info(f"{tool_name}_completed", duration_ms=duration)
            return result
        except TimeoutError:
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.error(
                f"{tool_name}_timeout",
                duration_ms=duration,
                timeout_s=TOOL_TIMEOUT_S,
            )
            return error_response(
                f"Operation timed out after {TOOL_TIMEOUT_S}s. "
                "The project may need re-indexing or the Voyage API may be slow. "
                "Try again or use a non-semantic search tool."
            )
        except Exception as e:
            duration = round((time.perf_counter() - start) * 1000, 2)
            log.exception(f"{tool_name}_failed", duration_ms=duration, error=str(e))
            raise

    return wrapper  # type: ignore[return-value]
