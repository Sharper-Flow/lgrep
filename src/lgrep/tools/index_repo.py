"""lgrep_index_symbols_repo tool implementation.

Indexes symbols from a GitHub repository via the GitHub REST API (no git clone).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import structlog

from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools._meta import error_response

log = structlog.get_logger()

# The fetch loop must finish strictly inside the tool wrapper's
# asyncio.wait_for(TOOL_TIMEOUT_S) and inside the MCP proxy's provider
# deadline, which starts earlier. Both clocks are lost races for an
# unbounded loop: the wrapper's structured timeout error never reaches the
# caller because the proxy tears the stdio session down first, and a stdio
# server exits on stdin EOF. The budget keeps a margin below both.
_DEADLINE_FRACTION = 0.8
_DEADLINE_FLOOR_S = 5.0

# File contents are fetched in concurrent waves of this size. The wave
# boundary is where the budget check counts unattempted files, so the
# deadline semantics stay exact under concurrency.
_FETCH_CONCURRENCY = 8


def _deadline_budget_s() -> float:
    """Total-operation budget as a fraction of the configured tool timeout."""
    timeout_s = float(os.environ.get("LGREP_TOOL_TIMEOUT_S", "45"))
    return max(_DEADLINE_FLOOR_S, _DEADLINE_FRACTION * timeout_s)


def _resolve_github_token(github_token: str | None) -> str | None:
    """Explicit parameter wins, then LGREP_GITHUB_TOKEN, then GITHUB_TOKEN.

    The anonymous rate limit (60/hour, shared across every session on the
    host) is the binding constraint for large indexes, so a deployment-level
    token lifts the ceiling without any per-call plumbing.
    """
    if github_token:
        return github_token
    return os.environ.get("LGREP_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _looks_like_local_path(value: str) -> bool:
    """Return True when a rejected owner/name value looks like a filesystem path."""
    if value.startswith(("/", "./", "../", "~/")) or "\\" in value:
        return True
    try:
        return Path(value).exists()
    except OSError:
        return False


async def index_repo(
    repo: str,
    ref: str = "HEAD",
    storage_dir: Path | str | None = None,
    max_files: int = 500,
    github_token: str | None = None,
) -> dict:
    """Index symbols from a GitHub repository via the REST API.

    Args:
        repo: GitHub repo in "owner/name" format (e.g. "anomalyco/lgrep")
        ref: Branch, tag, or commit SHA to index (default: "HEAD")
        storage_dir: Optional override for the symbol index storage directory
        max_files: Maximum number of files to index (default: 500)
        github_token: Optional GitHub personal access token for private repos

    Returns:
        Dict with files_indexed, symbols_indexed, and repo
    """
    t0 = time.monotonic()
    if "/" not in repo or repo.count("/") != 1:
        message = f"Invalid repo format. Expected 'owner/name', got: {repo!r}"
        if _looks_like_local_path(repo):
            message += (
                " This looks like a local path;"
                " for a local repository use lgrep_index_symbols_folder."
            )
        return error_response(message)

    try:
        import httpx
    except ImportError:
        return error_response(
            "httpx is required for GitHub repo indexing. Install with: pip install httpx",
        )

    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return error_response(
            "tree-sitter-language-pack is required. Install with: pip install tree-sitter-language-pack",
        )

    from lgrep.parser.extractor import _extract_symbols_from_tree
    from lgrep.parser.languages import get_language_spec

    headers = {"Accept": "application/vnd.github.v3+json"}
    resolved_token = _resolve_github_token(github_token)
    if resolved_token:
        headers["Authorization"] = f"token {resolved_token}"

    store = IndexStore(storage_dir=storage_dir)
    repo_key = f"github:{repo}@{ref}"

    files_dict: dict[str, str] = {}
    symbols_dict: dict[str, dict] = {}
    files_processed = 0
    truncated = False
    truncation_reason: str | None = None

    budget_s = _deadline_budget_s()
    deadline = t0 + budget_s

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Get the file tree from GitHub
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
        try:
            resp = await client.get(tree_url, timeout=min(30.0, budget_s))
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return error_response(
                f"GitHub API error: {e.response.status_code} for {repo}",
            )
        except httpx.RequestError as e:
            return error_response(
                f"Network error fetching {repo}: {e}",
            )

        tree_data = resp.json()
        if tree_data.get("truncated"):
            log.warning("github_tree_truncated", repo=repo)

        blob_items = [item for item in tree_data.get("tree", []) if item.get("type") == "blob"]

        # Select indexable files in tree order up to max_files. When the tree
        # holds more indexable files than the cap allows, the run is
        # truncated by max_files rather than silently incomplete.
        eligible: list[tuple[str, object]] = []
        eligible_exhausted = True
        for item in blob_items:
            file_path = item["path"]
            spec = get_language_spec(Path(file_path).suffix.lower())
            if spec is None:
                continue
            if len(eligible) >= max_files:
                eligible_exhausted = False
                break
            eligible.append((file_path, spec))
        if not eligible_exhausted:
            truncated = True
            truncation_reason = "max_files"

        async def _fetch(file_path: str, timeout: float):
            content_url = f"https://raw.githubusercontent.com/{repo}/{ref}/{file_path}"
            content_resp = await client.get(content_url, timeout=timeout)
            content_resp.raise_for_status()
            return file_path, content_resp.content

        # Fetch in bounded waves. The wave boundary is where the budget is
        # checked: expired with unattempted files means stop, save the
        # partial index, and report truncated with the deadline reason.
        for wave_start in range(0, len(eligible), _FETCH_CONCURRENCY):
            wave = eligible[wave_start : wave_start + _FETCH_CONCURRENCY]

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                truncated = True
                truncation_reason = "deadline"
                log.info(
                    "index_repo_deadline_reached",
                    repo=repo,
                    budget_s=round(budget_s, 2),
                    files_indexed=files_processed,
                    symbols_indexed=len(symbols_dict),
                    files_unattempted=len(eligible) - wave_start,
                )
                break

            wave_timeout = min(30.0, remaining)
            results = await asyncio.gather(
                *(_fetch(file_path, wave_timeout) for file_path, _spec in wave),
                return_exceptions=True,
            )

            for (file_path, spec), result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    if isinstance(result, (httpx.HTTPStatusError, httpx.RequestError)):
                        log.warning("github_file_fetch_failed", file=file_path, error=str(result))
                    else:
                        log.warning(
                            "github_file_fetch_unexpected", file=file_path, error=str(result)
                        )
                    continue

                _file_path, content = result
                file_hash = hashlib.sha256(content).hexdigest()
                files_dict[file_path] = file_hash

                # Parse symbols
                try:
                    parser = get_parser(spec.name)
                    tree = parser.parse(content)
                    syms = _extract_symbols_from_tree(tree.root_node, content, file_path, spec)
                    for sym in syms:
                        symbol_id = sym.id
                        if symbol_id in symbols_dict:
                            symbol_id = f"{sym.id}@{sym.start_byte}"

                        symbols_dict[symbol_id] = {
                            "id": symbol_id,
                            "name": sym.name,
                            "kind": sym.kind,
                            "file_path": sym.file_path,
                            "start_byte": sym.start_byte,
                            "end_byte": sym.end_byte,
                            "docstring": sym.docstring,
                            "decorators": sym.decorators,
                            "parent": sym.parent,
                        }
                except Exception as e:
                    log.warning("github_parse_failed", file=file_path, error=str(e))

                files_processed += 1

    index = CodeIndex(
        repo_path=repo_key,
        files=files_dict,
        symbols=symbols_dict,
    )
    store.save(index)

    return {
        "repo": repo,
        "ref": ref,
        "files_indexed": files_processed,
        "symbols_indexed": len(symbols_dict),
        "truncated": truncated,
        "truncation_reason": truncation_reason,
    }
