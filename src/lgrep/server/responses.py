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

from typing import Any

from typing_extensions import TypedDict

# --------------------------------------------------------------------------- #
# TypedDict definitions
# --------------------------------------------------------------------------- #


class ToolError(TypedDict):
    """Error response returned by any tool on failure."""

    error: str


class SearchSemanticResult(TypedDict):
    """Response for search_semantic.

    Fields:
      - ``results``: list of ``SearchChunk`` entries (may be empty).
      - ``total``: always equals ``len(results)`` for this response; it is
        not the corpus chunk count.
      - ``query``: the original query string echoed back.
      - ``path``: the project path the search ran against.
      - ``engine``: enum — ``"hybrid"`` when the handler ran a hybrid
        (vector + keyword) search, ``"vector"`` when vector-only.
    """

    results: list[SearchChunk]
    total: int
    query: str
    path: str
    engine: str


class _SearchChunkRequired(TypedDict):
    """Required fields for a semantic search result chunk."""

    file_path: str
    line_number: int  # required — mapped from SearchResult.start_line
    content: str
    score: float


class SearchChunk(_SearchChunkRequired, total=False):
    """A single semantic search result.

    Required keys (always present):
      - ``file_path``: repo-relative path of the matching file.
      - ``line_number``: primary line anchor (mapped from ``start_line``).
      - ``content``: matched chunk text.
      - ``score``: relevance score from the underlying engine.

    Optional fidelity keys (may be absent):
      - ``start_line`` / ``end_line``: original chunk line range.
      - ``match_type``: ``"hybrid"`` | ``"vector"`` | ``"keyword"``.
    """

    start_line: int  # optional fidelity — original range start
    end_line: int  # optional fidelity — original range end
    match_type: str  # optional fidelity — "hybrid" | "vector" | "keyword"


class FileOutline(TypedDict):
    """A single file's symbol summary in a repo outline."""

    file_path: str
    symbols: list[Any]
    symbol_count: int


class IndexSemanticResult(TypedDict):
    """Response for index_semantic."""

    file_count: int
    chunk_count: int
    duration_ms: float
    total_tokens: int


class _StatusSemanticRequired(TypedDict):
    """Required response fields for status_semantic."""

    files: int
    chunks: int
    watching: bool
    project: str
    disk_cache: bool | None  # None = not applicable / no disk cache read
    error: str | None  # present only on error


class StatusSemanticResult(_StatusSemanticRequired, total=False):
    """Response for status_semantic (single-project or all-projects)."""

    summary_only: bool  # True when no-arg global status omits deep counts
    detail: str  # Human-readable note for summary-only entries


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
    files_deleted: int
    symbols_indexed: int
    occurrences_indexed: int
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
    files: list[FileOutline]
    total_files: int
    total_symbols: int
    _meta: _Meta


class SearchSymbolsResult(TypedDict):
    """Response for search_symbols."""

    results: list[Any]
    total_matches: int
    _meta: _Meta


class _SearchTextResultRequired(TypedDict):
    """Required response fields for search_text."""

    results: list[SearchTextMatch]
    max_results: int
    _meta: _Meta
    error: str


class SearchTextResult(_SearchTextResultRequired, total=False):
    """Response for search_text.

    ``error`` is always present. Success uses an empty string so FastMCP's
    structured-output layer never serializes an omitted optional string as
    ``null`` for clients that validate strictly.
    """


class SearchTextMatch(TypedDict):
    file_path: str
    line_number: int
    line: str


class ReferenceCandidate(TypedDict):
    """A single candidate occurrence in a reference lookup result."""

    id: str
    name: str
    file_path: str
    line_number: int
    line_text: str
    kind: str
    enclosing_symbol_id: str | None
    is_test_file: bool


class _SearchReferencesResultRequired(TypedDict):
    """Required response fields for search_references."""

    query: str
    usage_filter: str
    total_matches: int
    results: list[ReferenceCandidate]
    candidate_names: list[str]
    disclaimer: str
    _meta: _Meta


class SearchReferencesResult(_SearchReferencesResultRequired, total=False):
    """Response for search_references.

    ``error`` is present only on timeout or other structured failures so the
    response shape stays stable even when the operation cannot complete.
    """

    error: str


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


class PruneOrphanEntry(TypedDict):
    """Single orphan entry in a prune report."""

    path: str
    reason: str  # one of OrphanReason values; kept loose for cross-module reuse
    bytes: int
    project_path: str | None


class PruneFailureEntry(TypedDict):
    """Single failure entry in a prune report."""

    path: str
    error: str


class PruneOrphansResult(TypedDict):
    """Response for the prune_orphans maintenance tool.

    Mirrors ``lgrep.tools.prune_orphans.PruneReport`` at the MCP layer so
    all MCP tool responses share the same response-pattern convention.
    """

    dry_run: bool
    dirs_examined: int
    orphans: list[PruneOrphanEntry]
    skipped_active: list[str]
    deleted_dirs: int
    reclaimed_bytes: int
    failures: list[PruneFailureEntry]
    _meta: _Meta


class PruneSymbolsEntry(TypedDict):
    """Single stale symbol index entry in a prune report."""

    path: str
    reason: str  # one of StaleReason values; kept loose for cross-module reuse
    bytes: int
    repo_path: str | None


class PruneSymbolsFailureEntry(TypedDict):
    """Single failure entry in a symbol prune report."""

    path: str
    error: str


class PruneSymbolsResult(TypedDict):
    """Response for the prune_symbols maintenance tool.

    Mirrors ``lgrep.tools.prune_symbols.PruneSymbolsReport`` at the MCP layer
    so all MCP tool responses share the same response-pattern convention.
    """

    dry_run: bool
    files_examined: int
    stale_indexes: list[PruneSymbolsEntry]
    skipped_active: list[str]
    deleted_files: int
    reclaimed_bytes: int
    failures: list[PruneSymbolsFailureEntry]
    _meta: _Meta


class WorktreeInvalidationEntry(TypedDict):
    """Single worktree invalidation result entry."""

    path: str
    cache_dir: str
    alias_removed: bool
    cache_deleted: bool
    bytes_reclaimed: int
    error: str | None


class WorktreeInvalidationResult(TypedDict):
    """Response for invalidate_worktree_cache."""

    paths_cleaned: int
    bytes_reclaimed: int
    entries: list[WorktreeInvalidationEntry]
    _meta: _Meta


class LoadedProjectEntry(TypedDict):
    """A single loaded project in diagnostics."""

    path: str
    watching: bool


class TimeoutAbandonmentSummary(TypedDict):
    """Summary of timeout/abandonment states in diagnostics."""

    abandoned_count: int
    finished_after_abandon_count: int
    failed_after_abandon_count: int


class DiagnosticsResult(TypedDict):
    """Response for lgrep_diagnostics.

    Read-only diagnostic snapshot of the lgrep daemon state.
    Contains no secrets, env vars, or raw tracebacks.
    """

    pid: int
    uptime_seconds: float
    transport: str | None
    worker_max_threads: int
    active_job_count: int
    recent_job_count: int
    loaded_project_count: int
    loaded_projects: list[LoadedProjectEntry]
    active_jobs: list[dict[str, Any]]
    recent_jobs: list[dict[str, Any]]
    timeout_abandonment_summary: TimeoutAbandonmentSummary


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
