"""lgrep_index_symbols_folder tool implementation.

Indexes all symbols in a local folder and persists to IndexStore.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import structlog

from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.storage.token_tracker import estimate_savings
from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec
from lgrep.tools._meta import error_response, make_meta

log = structlog.get_logger()

_extractor = SymbolExtractor()


def index_folder(
    repo_path: str,
    storage_dir: Path | str | None = None,
    max_files: int = 500,
) -> dict:
    """Index all symbols in a local folder.

    Args:
        repo_path: Absolute path to the repository/folder root
        storage_dir: Optional override for the symbol index storage directory
        max_files: Maximum number of files to index (default: 500)

    Returns:
        Dict with files_indexed, symbols_indexed, repo_path, and _meta envelope
    """
    t0 = time.monotonic()
    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0),
        )

    store = IndexStore(storage_dir=storage_dir)

    # Walk source files
    from lgrep.discovery import FileDiscovery

    discovery = FileDiscovery(root)
    files_dict: dict[str, str] = {}  # relative_path → hash
    symbols_dict: dict[str, dict] = {}  # symbol_id → metadata

    files_processed = 0
    for file_path in discovery.find_files():
        if files_processed >= max_files:
            break
        if get_language_spec(file_path.suffix.lower()) is None:
            continue

        try:
            content = file_path.read_bytes()
        except OSError:
            continue

        rel_path = str(file_path.relative_to(root))
        file_hash = hashlib.sha256(content).hexdigest()
        files_dict[rel_path] = file_hash

        symbols = _extractor.extract(file_path, repo_root=root)
        for sym in symbols:
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

        files_processed += 1

    index = CodeIndex(
        repo_path=str(root.resolve()),
        files=files_dict,
        symbols=symbols_dict,
    )
    store.save(index)

    tokens_saved = estimate_savings(len(symbols_dict))
    log.info(
        "index_folder_complete",
        repo=str(root),
        files=files_processed,
        symbols=len(symbols_dict),
    )

    return {
        "repo_path": str(root.resolve()),
        "files_indexed": files_processed,
        "symbols_indexed": len(symbols_dict),
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
