"""lgrep_index_symbols_folder tool implementation.

Indexes all symbols in a local folder and persists to IndexStore.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import structlog

from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec
from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

log = structlog.get_logger()

_extractor = SymbolExtractor()


def index_folder(
    repo_path: str,
    storage_dir: Path | str | None = None,
    max_files: int = 500,
    incremental: bool = True,
) -> dict:
    """Index all symbols in a local folder.

    Args:
        repo_path: Absolute path to the repository/folder root
        storage_dir: Optional override for the symbol index storage directory
        max_files: Maximum number of files to index (default: 500)
        incremental: If True (default), skip files whose SHA-256 hash matches
                     the stored index — only re-parse changed/new files.
                     Set to False to force a full re-index.

    Returns:
        Dict with files_indexed, symbols_indexed, files_skipped, repo_path,
        and _meta envelope
    """
    t0 = time.monotonic()

    # Input validation
    if not repo_path or not repo_path.strip():
        return error_response("repo_path must not be empty", _meta=make_meta(t0))

    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0),
        )

    store = IndexStore(storage_dir=storage_dir)
    resolved_root = str(root.resolve())

    # Load existing index for incremental comparison
    existing_index = store.load(resolved_root) if incremental else None
    existing_files = existing_index.files if existing_index else {}
    existing_symbols = dict(existing_index.symbols) if existing_index else {}

    # Walk source files
    from lgrep.discovery import FileDiscovery

    discovery = FileDiscovery(root)
    files_dict: dict[str, str] = dict(existing_files)  # start from existing
    symbols_dict: dict[str, dict] = dict(existing_symbols)

    files_processed = 0
    files_skipped = 0
    for file_path in discovery.find_files():
        if files_processed + files_skipped >= max_files:
            break
        if get_language_spec(file_path.suffix.lower()) is None:
            continue

        try:
            content = file_path.read_bytes()
        except OSError:
            continue

        rel_path = str(file_path.relative_to(root))
        file_hash = hashlib.sha256(content).hexdigest()

        # Incremental skip: file unchanged
        if incremental and existing_files.get(rel_path) == file_hash:
            files_skipped += 1
            continue

        files_dict[rel_path] = file_hash

        # Remove old symbols for this file before re-parsing
        if incremental:
            symbols_dict = {
                sid: sdata
                for sid, sdata in symbols_dict.items()
                if sdata.get("file_path") != rel_path
            }

        symbols = _extractor.extract(file_path, repo_root=root)
        for sym in symbols:
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

        files_processed += 1

    index = CodeIndex(
        repo_path=resolved_root,
        files=files_dict,
        symbols=symbols_dict,
    )
    store.save(index)

    tokens_saved = estimate_savings(len(symbols_dict))
    log.info(
        "index_folder_complete",
        repo=str(root),
        files=files_processed,
        files_skipped=files_skipped,
        symbols=len(symbols_dict),
        incremental=incremental,
    )

    return {
        "repo_path": resolved_root,
        "files_indexed": files_processed,
        "files_skipped": files_skipped,
        "symbols_indexed": len(symbols_dict),
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
