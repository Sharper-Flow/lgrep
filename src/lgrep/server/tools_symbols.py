"""Symbol and maintenance tools for the lgrep MCP server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import Context  # noqa: TC002
from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import mcp, time_tool
from lgrep.server.responses import (
    GetFileOutlineResult,
    GetFileTreeResult,
    GetRepoOutlineResult,
    GetSymbolsResult,
    InvalidateCacheResult,
    ListReposResult,
    SearchReferencesResult,
    SearchSymbolsResult,
    SearchTextResult,
    SymbolIndexJobResult,
    SymbolIndexSubmissionResult,
    ToolError,
    error_response,
)
from lgrep.server.runtime import SYMBOL_LANE
from lgrep.server.tools_maintenance import _destructive_grant_present
from lgrep.storage.index_store import normalize_repo_key
from lgrep.symbol_worker import SymbolWorkerLost
from lgrep.tools._meta import make_meta
from lgrep.tools.get_file_outline import get_file_outline as _get_file_outline
from lgrep.tools.get_file_tree import get_file_tree as _get_file_tree
from lgrep.tools.get_repo_outline import get_repo_outline as _get_repo_outline
from lgrep.tools.get_symbol import get_symbols as _get_symbols
from lgrep.tools.index_repo import _resolve_github_token as resolve_github_token
from lgrep.tools.index_repo import credential_values, redact_credentials
from lgrep.tools.invalidate_cache import invalidate_cache as _invalidate_cache
from lgrep.tools.list_repos import list_repos as _list_repos
from lgrep.tools.search_references import search_references as _search_references
from lgrep.tools.search_symbols import search_symbols as _search_symbols
from lgrep.tools.search_text import search_text as _search_text

if TYPE_CHECKING:
    from lgrep.server.lifecycle import LgrepContext


# ---------------------------------------------------------------------------
# Symbol work runs in the daemon's symbol worker, never in its interpreter.
# ---------------------------------------------------------------------------


def _daemon(ctx: Context | None) -> LgrepContext | None:
    """Return the daemon context, or None for a call outside an MCP request."""
    if ctx is None:
        return None
    try:
        return ctx.request_context.lifespan_context
    except ValueError:  # FastMCP: "Context is not available outside of a request"
        return None


async def _in_worker(app_ctx: LgrepContext, job_kind, caller, project, op, /, *args, **kwargs):
    """Run one symbol operation in the worker, supervised on the symbol lane.

    Positional-only, so a tool argument such as ``kind`` reaches the operation.
    """
    worker = app_ctx.runtime.symbol_worker
    # Bind the operation first: run_blocking owns the names kind, caller, and project.
    return await app_ctx.runtime.run_blocking(
        job_kind, caller, project, partial(worker.call, op, *args, **kwargs), lane=SYMBOL_LANE
    )


async def _run_symbol(ctx: Context | None, caller, project, local, /, *args, **kwargs):
    """Run a symbol tool's helper in the worker, or in-process outside the daemon."""
    app_ctx = _daemon(ctx)
    if app_ctx is None:
        return await asyncio.to_thread(local, *args, **kwargs)
    return await _in_worker(app_ctx, caller, caller, project, caller, *args, **kwargs)


def _worker_lost(exc: SymbolWorkerLost) -> ToolError:
    return ToolError(error=str(exc), code="symbol_worker_restarted", retryable=True)


def _release_snapshot(app_ctx: LgrepContext, task: asyncio.Task) -> None:
    """Release a finished acquisition's snapshot once no query reads it."""
    if task in app_ctx._symbol_index_readers or not task.done() or task.cancelled():
        return
    if task.exception() is None:
        app_ctx.runtime.symbol_worker.release(task.result())


def _symbol_index_done(app_ctx: LgrepContext, path: str, task: asyncio.Task) -> None:
    if app_ctx._symbol_index_tasks.get(path) is task:
        del app_ctx._symbol_index_tasks[path]
    # RuntimeSupervisor records failures; retrieve detached task exceptions too.
    if not task.cancelled():
        task.exception()
    _release_snapshot(app_ctx, task)


@asynccontextmanager
async def _symbol_index_snapshot(app_ctx: LgrepContext, path: str):
    """Yield the worker's checked-snapshot token, or None while acquisition runs."""
    from lgrep.server import TOOL_TIMEOUT_S

    readers = app_ctx._symbol_index_readers
    async with app_ctx._lock:
        if app_ctx._closed:
            raise RuntimeError("lgrep context is shutting down")
        task = app_ctx._symbol_index_tasks.get(path)
        if task is None:
            task = asyncio.create_task(
                _in_worker(app_ctx, "symbol_index", "search_symbols", path, "acquire", path),
                name=f"symbol_index:{path}",
            )
            app_ctx._symbol_index_tasks[path] = task
            task.add_done_callback(partial(_symbol_index_done, app_ctx, path))
        readers[task] = readers.get(task, 0) + 1
    try:
        # Waiting/cancelling a query never cancels the registered physical writer.
        done, _ = await asyncio.wait((task,), timeout=min(5.0, TOOL_TIMEOUT_S / 2))
        if app_ctx._closed:
            raise RuntimeError("lgrep context is shutting down")
        yield task.result() if done else None
    finally:
        readers[task] -= 1
        if not readers[task]:
            del readers[task]
        _release_snapshot(app_ctx, task)


# ---------------------------------------------------------------------------
# Explicit symbol jobs require a process-lifetime stdio context.
# ---------------------------------------------------------------------------


def _explicit_context(ctx: Context | None):
    if ctx is None:
        return None
    try:
        app = ctx.request_context.lifespan_context
    except (AttributeError, LookupError, RuntimeError, ValueError):
        return None
    return app if getattr(app, "transport", None) == "stdio" and not app._closed else None


def _jobs_unavailable() -> ToolError:
    return ToolError(
        error="Symbol index jobs require a running stdio daemon context.",
        code="symbol_index_jobs_unavailable",
        retryable=False,
    )


def _submit_explicit(app, caller, target, identity, work, t0, credentials=()):
    from lgrep.storage import index_store

    # The private hash includes the lexical store identity and credential snapshot.
    # Do not resolve paths, create stores, or probe the filesystem on the MCP loop.
    key = hashlib.sha256(
        json.dumps([caller, target, str(index_store.DEFAULT_SYMBOLS_DIR), *identity]).encode()
    ).hexdigest()
    outcome, job = app.runtime.submit_symbol_index(
        caller=caller, project=target, request_key=key, work=work, credentials=credentials
    )
    if outcome == "unavailable":
        return _jobs_unavailable()
    if outcome == "busy":
        return ToolError(
            error="Another explicit symbol index job is still running. Retry later.",
            code="symbol_index_busy",
            retryable=True,
        )
    return SymbolIndexSubmissionResult(
        job_id=job["job_id"],
        state=job["state"],
        duplicate=outcome == "duplicate",
        _meta=make_meta(t0, caller),
    )


_EXPLICIT_BUDGET_DESCRIPTION = (
    "max_files is the file cap. Jobs also have a default 150-second cooperative budget, configurable with "
    "LGREP_EXPLICIT_INDEX_BUDGET_S. Deadline stops appear in get_symbol_index_job status as "
    "truncation_reason='deadline'; capped results are not full repository coverage."
)


@mcp.tool(
    description=(
        "Submit a background local symbol-index job (stdio only). Returns job_id, not counters. "
        "Read get_symbol_index_job for final results before symbol lookups. "
        + _EXPLICIT_BUDGET_DESCRIPTION
        + " "
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
        Field(description="Maximum changed/new files to parse. " + _EXPLICIT_BUDGET_DESCRIPTION),
    ] = 500,
    incremental: Annotated[
        bool,
        Field(description="Skip unchanged files when true; set false to force full rebuild."),
    ] = True,
    ctx: Context | None = None,
) -> SymbolIndexSubmissionResult | ToolError:
    """Acknowledge a detached local job; all filesystem work stays on its worker."""
    t0 = time.monotonic()
    app = _explicit_context(ctx)
    if app is None:
        return _jobs_unavailable()
    if not path.strip() or "\x00" in path or max_files < 1:
        return error_response("path must be nonempty and max_files must be positive.")
    target = os.path.abspath(path)
    credentials = credential_values()
    if redact_credentials(target, credentials) != target:
        return error_response("Index targets must not contain credentials.")

    worker = app.runtime.symbol_worker

    def work(deadline, _stop_event):
        # The worker stops this build through its own shutdown signal.
        return worker.call(
            "index_folder", target, max_files=max_files, incremental=incremental, deadline=deadline
        )

    return _submit_explicit(
        app, "index_symbols_folder", target, [max_files, incremental], work, t0, credentials
    )


@mcp.tool(
    description=(
        "Submit a background GitHub symbol-index job (stdio only), without cloning. "
        "Returns job_id, not counters. Read get_symbol_index_job for final results. "
        + _EXPLICIT_BUDGET_DESCRIPTION
        + " "
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
        Field(
            description="Maximum remote files to fetch and parse. " + _EXPLICIT_BUDGET_DESCRIPTION,
        ),
    ] = 500,
    github_token: Annotated[
        str | None,
        Field(
            description=(
                "Optional token for private repos or higher GitHub API limits. "
                "When omitted, LGREP_GITHUB_TOKEN then GITHUB_TOKEN from the "
                "environment is used."
            ),
        ),
    ] = None,
    ctx: Context | None = None,
) -> SymbolIndexSubmissionResult | ToolError:
    """Acknowledge remote work owned by the dedicated explicit worker."""
    t0 = time.monotonic()
    app = _explicit_context(ctx)
    if app is None:
        return _jobs_unavailable()
    if repo.count("/") != 1 or not all(repo.split("/")) or repo.startswith((".", "~")):
        message = "Invalid repo format. Expected GitHub 'owner/name'."
        if repo.startswith(("/", "./", "../", "~/")) or "\\" in repo:
            message += " Use index_symbols_folder for local paths."
        return error_response(message)
    if not ref.strip() or "\x00" in repo + ref or max_files < 1:
        return error_response("ref must be nonempty and max_files must be positive.")
    token = resolve_github_token(github_token or None)
    credentials = credential_values((token,) if token else ())
    if any(redact_credentials(value, credentials) != value for value in (repo, ref)):
        return error_response("Index targets must not contain credentials.")

    worker = app.runtime.symbol_worker

    def work(deadline, _stop_event):
        # The worker stops this build through its own shutdown signal.
        return worker.call(
            "index_repo",
            repo,
            ref=ref,
            max_files=max_files,
            github_token=token or "",
            deadline=deadline,
        )

    return _submit_explicit(
        app, "index_symbols_repo", repo, [ref, max_files, token], work, t0, credentials
    )


@mcp.tool(
    description=(
        "Read an explicit symbol-index job in this stdio daemon. States: queued, running, "
        "completed, failed, cancelled (shutdown only). Final counters and truncation appear here. "
        "The last 100 terminal explicit jobs survive unrelated queries, but not daemon restart. "
        "Unknown or evicted IDs create no work. No public cancellation tool exists."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_symbol_index_job(
    job_id: str, ctx: Context | None = None
) -> SymbolIndexJobResult | ToolError:
    """Read detached work without waiting for or cancelling its worker."""
    t0 = time.monotonic()
    app = _explicit_context(ctx)
    if app is None:
        return _jobs_unavailable()
    job = app.runtime.get_symbol_index_job(job_id)
    if job is None:
        return ToolError(
            error="Unknown symbol index job in this daemon.",
            code="unknown_symbol_index_job",
            retryable=False,
        )
    return SymbolIndexJobResult(**job, _meta=make_meta(t0, "get_symbol_index_job"))


@mcp.tool(
    description=(
        "List repositories currently indexed in the symbol store. "
        "Use to discover available repository keys before symbol queries. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def list_repos(ctx: Context | None = None) -> ListReposResult:
    """List all repositories that have been indexed in the symbol store.

    Returns:
        Repos list and meta envelope.
    """
    t0 = time.monotonic()
    # Listing can parse an index body whose sidecar is missing.
    result = await _run_symbol(ctx, "list_repos", None, _list_repos)
    return ListReposResult(
        repos=result["repos"],
        _meta=make_meta(t0, "list_repos"),
    )


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
) -> GetFileTreeResult | ToolError:
    """Get the file tree of a repository, respecting .gitignore.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to return (default: 500)

    Returns:
        Files list, total count, and meta envelope.
    """
    t0 = time.monotonic()
    result = await asyncio.to_thread(_get_file_tree, path, max_files=max_files)
    if "error" in result:
        return error_response(result["error"])
    return GetFileTreeResult(
        files=result["files"],
        total_files=result["total_files"],
        _meta=make_meta(t0, "get_file_tree"),
    )


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
    ctx: Context | None = None,
) -> GetFileOutlineResult | ToolError:
    """Get the symbol outline (functions, classes, methods) for a single file.

    Args:
        path: Absolute path to the source file
        repo_root: Optional repo root for relative path computation in symbol IDs

    Returns:
        File path, symbols list, count, and meta envelope.
    """
    t0 = time.monotonic()
    try:
        result = await _run_symbol(
            ctx, "get_file_outline", path, _get_file_outline, path, repo_root=repo_root
        )
    except SymbolWorkerLost as exc:
        return _worker_lost(exc)
    if "error" in result:
        return error_response(result["error"])
    return GetFileOutlineResult(
        file_path=result["file_path"],
        symbols=result["symbols"],
        symbol_count=result["symbol_count"],
        _meta=make_meta(t0, "get_file_outline"),
    )


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
    ctx: Context | None = None,
) -> GetRepoOutlineResult | ToolError:
    """Get the symbol outline across an entire repository.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        Repo path, files list, counts, and meta envelope.
    """
    t0 = time.monotonic()
    try:
        result = await _run_symbol(
            ctx, "get_repo_outline", path, _get_repo_outline, path, max_files=max_files
        )
    except SymbolWorkerLost as exc:
        return _worker_lost(exc)
    if "error" in result:
        return error_response(result["error"])
    return GetRepoOutlineResult(
        repo_path=result["repo_path"],
        files=result["files"],
        total_files=result["total_files"],
        total_symbols=result["total_symbols"],
        _meta=make_meta(t0, "get_repo_outline"),
    )


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
    ctx: Context | None = None,
) -> SearchSymbolsResult | ToolError:
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
        Results list, total matches, and meta envelope.
    """
    t0 = time.monotonic()
    if not query or not query.strip():
        return error_response("query must not be empty")
    app_ctx = _daemon(ctx)
    if app_ctx is None:
        result = await asyncio.to_thread(_search_symbols, query, path, limit=limit, kind=kind)
    else:
        repo_key = normalize_repo_key(path)
        try:
            async with _symbol_index_snapshot(app_ctx, repo_key) as token:
                if token is None:
                    return ToolError(
                        error=(
                            "Symbol index check or refresh is still running. "
                            "See lgrep_diagnostics for its symbol_index job; "
                            "query again after it finishes."
                        ),
                        code="indexing_in_progress",
                        retryable=True,
                    )
                result = await _in_worker(
                    app_ctx,
                    "search_symbols",
                    "search_symbols",
                    repo_key,
                    "search_symbols",
                    token,
                    query,
                    repo_key,
                    limit=limit,
                    kind=kind,
                )
        except SymbolWorkerLost as exc:
            return _worker_lost(exc)
    if "error" in result:
        return error_response(result["error"])
    return SearchSymbolsResult(
        results=result["results"],
        total_matches=result["total_matches"],
        index_refreshed=result.get("index_refreshed", False),
        _meta=make_meta(t0, "search_symbols"),
    )


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
    limit: Annotated[
        int,
        Field(description="Maximum number of text matches to return."),
    ] = 50,
    case_sensitive: Annotated[
        bool,
        Field(description="When true, match text with case sensitivity."),
    ] = False,
    ctx: Context | None = None,
) -> SearchTextResult:
    """Search for literal text across all source files in a repository.

    Args:
        query: Text to search for
        path: Absolute path to the repository root
        limit: Maximum number of results to return (default: 50)
        case_sensitive: Whether to perform case-sensitive matching (default: False)

    Returns:
        Results list and meta envelope.
    """
    t0 = time.monotonic()
    if ctx is not None:
        app_ctx = ctx.request_context.lifespan_context
        result = await app_ctx.runtime.run_blocking(
            "search_text",
            "search_text",
            str(Path(path).resolve()),
            _search_text,
            query,
            path,
            max_results=limit,
            case_sensitive=case_sensitive,
        )
    else:
        result = await asyncio.to_thread(
            _search_text, query, path, max_results=limit, case_sensitive=case_sensitive
        )

    if "error" in result:
        return SearchTextResult(
            results=[],
            limit=limit,
            _meta=make_meta(t0, "search_text"),
            error=result["error"],
        )

    return SearchTextResult(
        results=result["results"],
        limit=limit,
        _meta=make_meta(t0, "search_text"),
        error="",
    )


@mcp.tool(
    description=(
        "Get metadata and source for one or more symbol IDs from the symbol index. "
        "Use after symbol search to read implementation bodies; pass every relevant ID in one call. "
        "Each requested ID returns one entry in order. An unknown ID or a stale source range "
        "returns {id, error} in its entry instead of a top-level error, so check each entry. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def get_symbols(
    symbol_ids: Annotated[
        list[str],
        Field(description="One or more symbol IDs in file_path:kind:name format."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the indexed local repository root."),
    ],
    ctx: Context | None = None,
) -> GetSymbolsResult | ToolError:
    """Get full metadata and source code for one or more symbols in one call.

    Args:
        symbol_ids: List of stable symbol IDs (format: "file_path:kind:name")
        path: Absolute path to the indexed repository

    Returns:
        Symbols list and meta envelope. Missing IDs and source-range
        mismatches appear as per-entry ``{"id", "error"}`` dicts.
    """
    t0 = time.monotonic()
    try:
        result = await _run_symbol(ctx, "get_symbols", path, _get_symbols, symbol_ids, path)
    except SymbolWorkerLost as exc:
        return _worker_lost(exc)
    if "error" in result:
        return error_response(result["error"])
    return GetSymbolsResult(
        symbols=result["symbols"],
        _meta=make_meta(t0, "get_symbols"),
    )


@mcp.tool(
    description=(
        "Search for candidate references/usages of a symbol name in an indexed Python repository. "
        "Results are bounded and explicitly labeled as candidate occurrences, not compiler-accurate "
        "or exhaustive references. Supports production-first, include-tests, or tests-only filtering. "
        "Requires prior indexing with lgrep_index_symbols_folder. MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def search_references(
    query: Annotated[
        str,
        Field(description="Symbol name to look up (e.g. 'authenticate')."),
    ],
    path: Annotated[
        str,
        Field(description="Absolute path to the indexed repository root."),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of candidate occurrence results to return."),
    ] = 20,
    usage_filter: Annotated[
        str,
        Field(
            description=(
                "Result set filter: 'production_first' (default), 'include_tests', or 'tests_only'."
            ),
        ),
    ] = "production_first",
    kind: Annotated[
        str | None,
        Field(
            description="Optional occurrence kind filter: 'call', 'attribute', 'import', or 'reference'."
        ),
    ] = None,
    ctx: Context | None = None,
) -> SearchReferencesResult | ToolError:
    """Search for candidate usages of a symbol name in an indexed repository.

    Args:
        query: Symbol name to look up
        path: Absolute path to the indexed repository
        limit: Maximum number of results to return (default: 20)
        usage_filter: production_first, include_tests, or tests_only (default: production_first)
        kind: Optional occurrence kind filter
        ctx: Optional MCP context for RuntimeSupervisor routing

    Returns:
        Candidate occurrences, candidate names, disclaimer, and meta envelope.
    """
    t0 = time.monotonic()
    try:
        result = await _run_symbol(
            ctx,
            "search_references",
            str(Path(path).resolve()),
            _search_references,
            query,
            path,
            limit=limit,
            usage_filter=usage_filter,
            kind=kind,
        )
    except SymbolWorkerLost as exc:
        return _worker_lost(exc)

    if "error" in result:
        return error_response(result["error"])

    return SearchReferencesResult(
        query=result["query"],
        usage_filter=result["usage_filter"],
        total_matches=result["total_matches"],
        production_matches=result["production_matches"],
        test_matches=result["test_matches"],
        returned_production=result["returned_production"],
        returned_tests=result["returned_tests"],
        stale_file_count=result["stale_file_count"],
        results=result["results"],
        candidate_names=result["candidate_names"],
        disclaimer=result["disclaimer"],
        _meta=make_meta(t0, "search_references"),
    )


@mcp.tool(
    description=(
        "Delete a repository symbol index cache entry to force a full symbol re-index. "
        "Use when index corruption or schema drift is suspected. "
        "Deletion over MCP requires the server to set LGREP_ALLOW_DESTRUCTIVE_MCP=1; "
        "otherwise the call refuses deletion and says so. There is no CLI equivalent. "
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
    ctx: Context | None = None,
) -> InvalidateCacheResult:
    """Remove the symbol index for a repository, forcing a full re-index on next use.

    Destructive runs require the ``LGREP_ALLOW_DESTRUCTIVE_MCP`` grant on the
    server. Without it the handler refuses the deletion and reports why.
    """
    t0 = time.monotonic()
    if not _destructive_grant_present():
        return InvalidateCacheResult(
            status="refused",
            refused_reason=(
                "Destructive run refused: LGREP_ALLOW_DESTRUCTIVE_MCP is not set. "
                "Symbol index deletion is not available over MCP. "
                "Set LGREP_ALLOW_DESTRUCTIVE_MCP=1 on the server to allow destructive "
                "MCP calls. There is no CLI equivalent."
            ),
            _meta=make_meta(t0, "invalidate_cache"),
        )

    # Invalidation loads the index and evicts it from the worker's cache.
    result = await _run_symbol(ctx, "invalidate_cache", path, _invalidate_cache, path)
    return InvalidateCacheResult(
        status=result["status"],
        refused_reason="",
        _meta=make_meta(t0, "invalidate_cache"),
    )
