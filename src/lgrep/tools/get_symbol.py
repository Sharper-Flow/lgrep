"""lgrep_get_symbol and lgrep_get_symbols tool implementations.

Retrieves full symbol metadata and source code by symbol ID.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.tools._meta import error_response

log = structlog.get_logger()


def _github_repo_parts(repo_key: str) -> tuple[str, str] | None:
    """Parse github repo key: github:owner/name@ref."""
    if not repo_key.startswith("github:"):
        return None
    value = repo_key[len("github:") :]
    if "@" not in value:
        return None
    repo, ref = value.rsplit("@", 1)
    if not repo or not ref:
        return None
    return repo, ref


def _get_source_bytes(
    store: IndexStore,
    repo_key: str,
    sym_data: dict,
) -> bytes | None:
    """Load symbol source bytes from local file or GitHub raw content."""
    file_path = sym_data["file_path"]
    start_byte = sym_data["start_byte"]
    end_byte = sym_data["end_byte"]

    # Local repositories: read directly from disk.
    if not repo_key.startswith("github:"):
        full_path = Path(repo_key) / file_path
        return store.get_symbol_content(full_path, start_byte, end_byte)

    # GitHub repositories: fetch raw file and slice byte range.
    parts = _github_repo_parts(repo_key)
    if not parts:
        return None
    repo, ref = parts
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{file_path}"
    try:
        import httpx  # lazy import — only needed for GitHub repos

        resp = httpx.get(url, timeout=15.0)
        resp.raise_for_status()
    except Exception as e:
        log.warning("github_source_fetch_failed", url=url, error=str(e))
        return None
    return resp.content[start_byte:end_byte]


def get_symbol(
    symbol_id: str,
    repo_path: str,
    storage_dir: Path | str | None = None,
) -> dict:
    """Get full metadata and source code for a single symbol.

    Args:
        symbol_id: Stable symbol ID in format "file_path:kind:name"
        repo_path: Absolute path to the indexed repository
        storage_dir: Optional override for the symbol index storage directory

    Returns:
        Dict with symbol dict including the source field.
        Returns error dict if the repo is not indexed or the symbol is not found.
    """
    # Input validation
    if not symbol_id or not symbol_id.strip():
        return error_response("symbol_id must not be empty")

    store = IndexStore(storage_dir=storage_dir)

    repo_key = normalize_repo_key(repo_path)
    # First use of an unindexed local git checkout builds that checkout's
    # own index instead of refusing.
    if store.load(repo_key) is None:
        from lgrep.tools._index_bootstrap import ensure_symbol_index

        ensure_symbol_index(repo_path, storage_dir=storage_dir)
    index = store.load(repo_key)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
        )

    sym_data = index.symbols.get(symbol_id)
    if sym_data is None:
        return error_response(
            f"Symbol not found: {symbol_id}",
        )

    # Retrieve source bytes
    sym_data = dict(sym_data)  # copy to avoid mutating the index
    source_bytes = _get_source_bytes(store, repo_key, sym_data)
    sym_data["source"] = source_bytes.decode("utf-8", errors="replace") if source_bytes else None

    return {
        "symbol": sym_data,
    }


def get_symbols(
    symbol_ids: list[str],
    repo_path: str,
    storage_dir: Path | str | None = None,
) -> dict:
    """Get full metadata and source code for multiple symbols in one call.

    Args:
        symbol_ids: List of stable symbol IDs
        repo_path: Absolute path to the indexed repository
        storage_dir: Optional override for the symbol index storage directory

    Returns:
        Dict with a symbols list.
        Returns error dict if the repo is not indexed.
    """
    store = IndexStore(storage_dir=storage_dir)

    repo_key = normalize_repo_key(repo_path)
    # First use of an unindexed local git checkout builds that checkout's
    # own index instead of refusing.
    if store.load(repo_key) is None:
        from lgrep.tools._index_bootstrap import ensure_symbol_index

        ensure_symbol_index(repo_path, storage_dir=storage_dir)
    index = store.load(repo_key)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
        )

    symbols = []
    for sym_id in symbol_ids:
        sym_data = index.symbols.get(sym_id)
        if sym_data is None:
            symbols.append({"id": sym_id, "error": "not_found"})
            continue

        sym_data = dict(sym_data)
        source_bytes = _get_source_bytes(store, repo_key, sym_data)
        sym_data["source"] = (
            source_bytes.decode("utf-8", errors="replace") if source_bytes else None
        )
        symbols.append(sym_data)

    return {
        "symbols": symbols,
    }
