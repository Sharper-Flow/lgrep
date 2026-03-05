"""lgrep_index_symbols_repo tool implementation.

Indexes symbols from a GitHub repository via the GitHub REST API (no git clone).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import structlog

from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

log = structlog.get_logger()


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
        Dict with files_indexed, symbols_indexed, repo, and _meta envelope
    """
    t0 = time.monotonic()

    if "/" not in repo or repo.count("/") != 1:
        return error_response(
            f"Invalid repo format. Expected 'owner/name', got: {repo!r}",
            _meta=make_meta(t0),
        )

    try:
        import httpx
    except ImportError:
        return error_response(
            "httpx is required for GitHub repo indexing. Install with: pip install httpx",
            _meta=make_meta(t0),
        )

    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return error_response(
            "tree-sitter-language-pack is required. Install with: pip install tree-sitter-language-pack",
            _meta=make_meta(t0),
        )

    from lgrep.parser.extractor import _extract_symbols_from_tree
    from lgrep.parser.languages import get_language_spec

    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    store = IndexStore(storage_dir=storage_dir)
    repo_key = f"github:{repo}@{ref}"

    files_dict: dict[str, str] = {}
    symbols_dict: dict[str, dict] = {}
    files_processed = 0

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Get the file tree from GitHub
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
        try:
            resp = await client.get(tree_url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return error_response(
                f"GitHub API error: {e.response.status_code} for {repo}",
                _meta=make_meta(t0),
            )
        except httpx.RequestError as e:
            return error_response(
                f"Network error fetching {repo}: {e}",
                _meta=make_meta(t0),
            )

        tree_data = resp.json()
        if tree_data.get("truncated"):
            log.warning("github_tree_truncated", repo=repo)

        blob_items = [item for item in tree_data.get("tree", []) if item.get("type") == "blob"]

        for item in blob_items:
            if files_processed >= max_files:
                break

            file_path = item["path"]
            suffix = Path(file_path).suffix.lower()
            spec = get_language_spec(suffix)
            if spec is None:
                continue

            # Fetch file content
            content_url = f"https://raw.githubusercontent.com/{repo}/{ref}/{file_path}"
            try:
                content_resp = await client.get(content_url)
                content_resp.raise_for_status()
                content = content_resp.content
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                log.warning("github_file_fetch_failed", file=file_path, error=str(e))
                continue

            file_hash = hashlib.sha256(content).hexdigest()
            files_dict[file_path] = file_hash

            # Parse symbols
            try:
                parser = get_parser(spec.name)
                tree = parser.parse(content)
                from lgrep.parser.symbols import make_symbol_id

                syms = _extract_symbols_from_tree(tree.root_node, content, file_path, spec)
                for sym in syms:
                    symbols_dict[sym.id] = {
                        "id": sym.id,
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

    tokens_saved = estimate_savings(len(symbols_dict))
    return {
        "repo": repo,
        "ref": ref,
        "files_indexed": files_processed,
        "symbols_indexed": len(symbols_dict),
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
