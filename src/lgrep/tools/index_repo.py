"""lgrep_index_symbols_repo tool implementation.

Indexes symbols from a GitHub repository via the GitHub REST API (no git clone).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.exceptions import OperationCancelled
from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools._meta import error_response

if TYPE_CHECKING:
    import threading

log = structlog.get_logger()

# File contents are fetched in concurrent waves of this size. The wave
# boundary is where the budget check counts unattempted files, so the
# deadline semantics stay exact under concurrency.
_FETCH_CONCURRENCY = 8


def credential_values(extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Snapshot known credentials before admission or remote requests."""
    return tuple(
        sorted(
            {
                value
                for value in (*extra, os.getenv("LGREP_GITHUB_TOKEN"), os.getenv("GITHUB_TOKEN"))
                if value
            },
            key=len,
            reverse=True,
        )
    )


def redact_credentials(value: str, credentials: tuple[str, ...] = ()) -> str:
    """Redact before truncation so a boundary cannot expose part of a token."""
    for credential in credential_values(credentials):
        value = value.replace(credential, "[redacted]")
    return value


def _resolve_github_token(github_token: str | None) -> str | None:
    """Explicit parameter wins, then LGREP_GITHUB_TOKEN, then GITHUB_TOKEN.

    The anonymous rate limit (60/hour, shared across every session on the
    host) is the binding constraint for large indexes, so a deployment-level
    token lifts the ceiling without any per-call plumbing.
    """
    if github_token is not None:
        return github_token or None
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
    run_sync=None,
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
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
    credentials = credential_values((github_token,) if github_token else ())

    def check_stop():
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Symbol indexing stopped during shutdown")

    def remaining_timeout():
        return 30.0 if deadline is None else min(30.0, max(0.001, deadline - time.monotonic()))

    def expired():
        return deadline is not None and time.monotonic() >= deadline

    check_stop()
    if any(redact_credentials(value, credentials) != value for value in (repo, ref)):
        return error_response("Index targets must not contain credentials.")
    if "/" not in repo or repo.count("/") != 1:
        message = f"Invalid repo format. Expected 'owner/name', got: {repo!r}"
        if _looks_like_local_path(repo):
            message += (
                " This looks like a local path;"
                " for a local repository use lgrep_index_symbols_folder."
            )
        return error_response(redact_credentials(message, credentials))

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

    if run_sync is None:
        # Without a supervisor, parsing still leaves the event loop.
        async def run_sync(fn):  # noqa: F811 — local default runner
            return await asyncio.to_thread(fn)

    headers = {"Accept": "application/vnd.github.v3+json"}
    resolved_token = _resolve_github_token(github_token)
    if resolved_token:
        headers["Authorization"] = f"token {resolved_token}"

    store = IndexStore(storage_dir=storage_dir)
    repo_key = f"github:{repo}@{ref}"

    fetched: list[tuple[str, object, bytes]] = []
    truncated = False
    truncation_reason: str | None = None

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Get the file tree from GitHub
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
        if expired():
            truncated = True
            truncation_reason = "deadline"
            tree_data = {}
        else:
            try:
                resp = await client.get(tree_url, timeout=remaining_timeout())
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                return error_response(
                    redact_credentials(
                        f"GitHub API error: {e.response.status_code} for {repo}", credentials
                    )
                )
            except httpx.RequestError as e:
                if not expired():
                    return error_response(
                        redact_credentials(f"Network error fetching {repo}: {e}", credentials)
                    )
                truncated = True
                truncation_reason = "deadline"
                tree_data = {}
            else:
                tree_data = resp.json()
        check_stop()
        if tree_data.get("truncated"):
            log.warning("github_tree_truncated", repo=redact_credentials(repo, credentials))

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

            check_stop()
            if expired():
                truncated = True
                truncation_reason = "deadline"
                log.info(
                    "index_repo_deadline_reached",
                    repo=redact_credentials(repo, credentials),
                    files_fetched=len(fetched),
                    files_unattempted=len(eligible) - wave_start,
                )
                break

            wave_timeout = remaining_timeout()
            results = await asyncio.gather(
                *(_fetch(file_path, wave_timeout) for file_path, _spec in wave),
                return_exceptions=True,
            )

            for (file_path, spec), result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    if isinstance(result, httpx.TimeoutException) and expired():
                        truncated = True
                        truncation_reason = "deadline"
                    if isinstance(result, (httpx.HTTPStatusError, httpx.RequestError)):
                        log.warning(
                            "github_file_fetch_failed",
                            file=redact_credentials(file_path, credentials),
                            error=redact_credentials(str(result), credentials)[:500],
                        )
                    else:
                        log.warning(
                            "github_file_fetch_unexpected",
                            file=redact_credentials(file_path, credentials),
                            error=redact_credentials(str(result), credentials)[:500],
                        )
                    continue

                _file_path, content = result
                fetched.append((file_path, spec, content))

    def _parse_and_persist():
        nonlocal truncated, truncation_reason
        files_processed = 0
        files_dict: dict[str, str] = {}
        symbols_dict: dict[str, dict] = {}
        for file_path, spec, content in fetched:
            check_stop()
            if expired():
                truncated = True
                truncation_reason = "deadline"
                break
            file_hash = hashlib.sha256(content).hexdigest()
            files_dict[file_path] = file_hash
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
                log.warning(
                    "github_parse_failed",
                    file=redact_credentials(file_path, credentials),
                    error=redact_credentials(str(e), credentials)[:500],
                )
            files_processed += 1

        index = CodeIndex(
            repo_path=repo_key,
            files=files_dict,
            symbols=symbols_dict,
        )
        check_stop()
        store.save(index)
        return files_processed, len(symbols_dict)

    files_processed, symbols_count = await run_sync(_parse_and_persist)

    return {
        "repo": redact_credentials(repo, credentials),
        "ref": redact_credentials(ref, credentials),
        "files_indexed": files_processed,
        "symbols_indexed": symbols_count,
        "truncated": truncated,
        "truncation_reason": truncation_reason,
    }
