"""Symbol and maintenance tools for the lgrep MCP server."""

from __future__ import annotations

import asyncio
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from lgrep.server import mcp, time_tool
from lgrep.server.responses import (
    GetFileOutlineResult,
    GetFileTreeResult,
    GetRepoOutlineResult,
    GetSymbolResult,
    GetSymbolsResult,
    IndexSymbolsFolderResult,
    IndexSymbolsRepoResult,
    InvalidateCacheResult,
    ListReposResult,
    SearchSymbolsResult,
    SearchTextResult,
    error_response,
)
from lgrep.tools.get_file_outline import get_file_outline as _get_file_outline
from lgrep.tools.get_file_tree import get_file_tree as _get_file_tree
from lgrep.tools.get_repo_outline import get_repo_outline as _get_repo_outline
from lgrep.tools.get_symbol import get_symbol as _get_symbol
from lgrep.tools.get_symbol import get_symbols as _get_symbols
from lgrep.tools.index_folder import index_folder as _index_folder
from lgrep.tools.index_repo import index_repo as _index_repo
from lgrep.tools.invalidate_cache import invalidate_cache as _invalidate_cache
from lgrep.tools.list_repos import list_repos as _list_repos
from lgrep.tools.search_symbols import search_symbols as _search_symbols
from lgrep.tools.search_text import search_text as _search_text

# ---------------------------------------------------------------------------
# MCP Tools (11 symbol tools)
# ---------------------------------------------------------------------------


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
) -> IndexSymbolsFolderResult:
    """Index all symbols in a local folder for exact symbol lookup.

    Args:
        path: Absolute path to the repository/folder root
        max_files: Maximum number of files to index (default: 500)
        incremental: Skip files whose content hash matches the stored index
                     (default: True). Set to False to force a full re-index.

    Returns:
        Files indexed, skipped, symbols count, and repo path.
    """
    result = await asyncio.to_thread(
        _index_folder, path, max_files=max_files, incremental=incremental
    )
    return IndexSymbolsFolderResult(
        files_indexed=result["files_indexed"],
        files_skipped=result["files_skipped"],
        symbols_indexed=result["symbols_indexed"],
        repo_path=result["repo_path"],
        _meta={"duration_ms": 0.0, "tool": "index_symbols_folder"},
    )


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
) -> IndexSymbolsRepoResult:
    """Index symbols from a GitHub repository via the REST API (no git clone).

    Args:
        repo: GitHub repo in "owner/name" format (e.g. "anomalyco/lgrep")
        ref: Branch, tag, or commit SHA to index (default: "HEAD")
        max_files: Maximum number of files to index (default: 500)
        github_token: Optional GitHub personal access token for private repos

    Returns:
        Files indexed, symbols count, repo, and meta envelope.
    """
    result = await _index_repo(repo, ref=ref, max_files=max_files, github_token=github_token)
    return IndexSymbolsRepoResult(
        files_indexed=result["files_indexed"],
        symbols_indexed=result["symbols_indexed"],
        repo=result["repo"],
        _meta={"duration_ms": 0.0, "tool": "index_symbols_repo"},
    )


@mcp.tool(
    description=(
        "List repositories currently indexed in the symbol store. "
        "Use to discover available repository keys before symbol queries. "
        "MCP tool call only; do not invoke via shell."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
@time_tool
async def list_repos() -> ListReposResult:
    """List all repositories that have been indexed in the symbol store.

    Returns:
        Repos list and meta envelope.
    """
    result = await asyncio.to_thread(_list_repos)
    return ListReposResult(
        repos=result["repos"],
        _meta={"duration_ms": 0.0, "tool": "list_repos"},
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
) -> GetFileTreeResult:
    """Get the file tree of a repository, respecting .gitignore.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to return (default: 500)

    Returns:
        Files list, total count, and meta envelope.
    """
    result = await asyncio.to_thread(_get_file_tree, path, max_files=max_files)
    return GetFileTreeResult(
        files=result["files"],
        total_files=result["total_files"],
        _meta={"duration_ms": 0.0, "tool": "get_file_tree"},
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
) -> GetFileOutlineResult:
    """Get the symbol outline (functions, classes, methods) for a single file.

    Args:
        path: Absolute path to the source file
        repo_root: Optional repo root for relative path computation in symbol IDs

    Returns:
        File path, symbols list, count, and meta envelope.
    """
    result = await asyncio.to_thread(_get_file_outline, path, repo_root=repo_root)
    return GetFileOutlineResult(
        file_path=result["file_path"],
        symbols=result["symbols"],
        symbol_count=result["symbol_count"],
        _meta={"duration_ms": 0.0, "tool": "get_file_outline"},
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
) -> GetRepoOutlineResult:
    """Get the symbol outline across an entire repository.

    Args:
        path: Absolute path to the repository root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        Repo path, files list, counts, and meta envelope.
    """
    result = await asyncio.to_thread(_get_repo_outline, path, max_files=max_files)
    return GetRepoOutlineResult(
        repo_path=result["repo_path"],
        files=result["files"],
        total_files=result["total_files"],
        total_symbols=result["total_symbols"],
        _meta={"duration_ms": 0.0, "tool": "get_repo_outline"},
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
) -> SearchSymbolsResult:
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
    result = await asyncio.to_thread(_search_symbols, query, path, limit=limit, kind=kind)
    if "error" in result:
        return error_response(result["error"])
    return SearchSymbolsResult(
        results=result["results"],
        total_matches=result["total_matches"],
        _meta={"duration_ms": 0.0, "tool": "search_symbols"},
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
    max_results: Annotated[
        int,
        Field(description="Maximum number of text matches to return."),
    ] = 50,
    case_sensitive: Annotated[
        bool,
        Field(description="When true, match text with case sensitivity."),
    ] = False,
) -> SearchTextResult:
    """Search for literal text across all source files in a repository.

    Args:
        query: Text to search for
        path: Absolute path to the repository root
        max_results: Maximum number of results to return (default: 50)
        case_sensitive: Whether to perform case-sensitive matching (default: False)

    Returns:
        Results list and meta envelope.
    """
    result = await asyncio.to_thread(
        _search_text, query, path, max_results=max_results, case_sensitive=case_sensitive
    )
    return SearchTextResult(
        results=result["results"],
        max_results=max_results,
        _meta={"duration_ms": 0.0, "tool": "search_text"},
    )


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
) -> GetSymbolResult:
    """Get full metadata and source code for a single symbol by ID.

    Symbol IDs use the format "file_path:kind:name" (e.g. "src/auth.py:function:authenticate").
    Run lgrep_index_symbols_folder first to build the index.

    Args:
        symbol_id: Stable symbol ID in format "file_path:kind:name"
        path: Absolute path to the indexed repository

    Returns:
        Symbol dict and meta envelope.
    """
    result = await asyncio.to_thread(_get_symbol, symbol_id, path)
    if "error" in result:
        return error_response(result["error"])
    return GetSymbolResult(
        symbol=result["symbol"],
        _meta={"duration_ms": 0.0, "tool": "get_symbol"},
    )


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
) -> GetSymbolsResult:
    """Get full metadata and source code for multiple symbols in one call.

    Args:
        symbol_ids: List of stable symbol IDs (format: "file_path:kind:name")
        path: Absolute path to the indexed repository

    Returns:
        Symbols list and meta envelope.
    """
    result = await asyncio.to_thread(_get_symbols, symbol_ids, path)
    if "error" in result:
        return error_response(result["error"])
    return GetSymbolsResult(
        symbols=result["symbols"],
        _meta={"duration_ms": 0.0, "tool": "get_symbols"},
    )


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
) -> InvalidateCacheResult:
    """Remove the symbol index for a repository, forcing a full re-index on next use.

    Args:
        path: Absolute path to the repository root

    Returns:
        Status ("deleted" or "not_found") and meta envelope.
    """
    result = await asyncio.to_thread(_invalidate_cache, path)
    return InvalidateCacheResult(
        status=result["status"],
        _meta={"duration_ms": 0.0, "tool": "invalidate_cache"},
    )



