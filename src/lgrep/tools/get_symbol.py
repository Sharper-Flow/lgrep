"""lgrep_get_symbol and lgrep_get_symbols tool implementations.

Retrieves full symbol metadata and source code by symbol ID.
"""

from __future__ import annotations

import time
from pathlib import Path

from lgrep.storage.index_store import IndexStore
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta


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
        Dict with symbol dict (including source field) and _meta envelope.
        Returns error dict if the repo is not indexed or the symbol is not found.
    """
    t0 = time.monotonic()

    # Input validation
    if not symbol_id or not symbol_id.strip():
        return error_response("symbol_id must not be empty", _meta=make_meta(t0))

    store = IndexStore(storage_dir=storage_dir)

    resolved = str(Path(repo_path).resolve())
    index = store.load(resolved)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
            _meta=make_meta(t0),
        )

    sym_data = index.symbols.get(symbol_id)
    if sym_data is None:
        return error_response(
            f"Symbol not found: {symbol_id}",
            _meta=make_meta(t0),
        )

    # Retrieve source bytes
    sym_data = dict(sym_data)  # copy to avoid mutating the index
    file_path = Path(resolved) / sym_data["file_path"]
    source_bytes = store.get_symbol_content(
        file_path,
        sym_data["start_byte"],
        sym_data["end_byte"],
    )
    sym_data["source"] = source_bytes.decode("utf-8", errors="replace") if source_bytes else None

    tokens_saved = estimate_savings(1)
    return {
        "symbol": sym_data,
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
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
        Dict with symbols list and _meta envelope.
        Returns error dict if the repo is not indexed.
    """
    t0 = time.monotonic()
    store = IndexStore(storage_dir=storage_dir)

    resolved = str(Path(repo_path).resolve())
    index = store.load(resolved)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
            _meta=make_meta(t0),
        )

    symbols = []
    for sym_id in symbol_ids:
        sym_data = index.symbols.get(sym_id)
        if sym_data is None:
            symbols.append({"id": sym_id, "error": "not_found"})
            continue

        sym_data = dict(sym_data)
        file_path = Path(resolved) / sym_data["file_path"]
        source_bytes = store.get_symbol_content(
            file_path,
            sym_data["start_byte"],
            sym_data["end_byte"],
        )
        sym_data["source"] = (
            source_bytes.decode("utf-8", errors="replace") if source_bytes else None
        )
        symbols.append(sym_data)

    tokens_saved = estimate_savings(len(symbols))
    return {
        "symbols": symbols,
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
