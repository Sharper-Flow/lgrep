"""File and repo outline builders for lgrep.

Builds structured summaries of symbols in a file or across a repository.
Used by lgrep_get_file_outline and lgrep_get_repo_outline tools.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import structlog

from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec

log = structlog.get_logger()

_extractor = SymbolExtractor()


def build_file_outline(file_path: Path | str, repo_root: Path | None = None) -> dict:
    """Build a structured outline of symbols in a single file.

    Args:
        file_path: Path to the source file
        repo_root: Optional repo root for relative path computation

    Returns:
        Dict with:
            file_path: str — path to the file
            symbols: list[dict] — list of symbol dicts (name, kind, id, start_byte, end_byte)
            symbol_count: int — total number of symbols
    """
    file_path = Path(file_path)
    symbols = _extractor.extract(file_path, repo_root=repo_root)

    seen_ids: set[str] = set()
    serialized_symbols = []
    for s in symbols:
        symbol_id = s.id
        if symbol_id in seen_ids:
            symbol_id = f"{s.id}@{s.start_byte}"
        seen_ids.add(symbol_id)
        serialized_symbols.append(
            {
                "id": symbol_id,
                "name": s.name,
                "kind": s.kind,
                "start_byte": s.start_byte,
                "end_byte": s.end_byte,
                "docstring": s.docstring,
                "decorators": s.decorators,
                "parent": s.parent,
            }
        )

    return {
        "file_path": str(file_path),
        "symbols": serialized_symbols,
        "symbol_count": len(symbols),
    }


def build_repo_outline(
    repo_path: Path | str,
    max_files: int = 500,
) -> dict:
    """Build a structured outline of symbols across a repository.

    Walks the repo directory, extracts symbols from each supported source file,
    and returns an aggregated outline.

    Args:
        repo_path: Path to the repository root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        Dict with:
            repo_path: str — path to the repo root
            files: list[dict] — list of file outlines
            total_files: int — number of files processed
            total_symbols: int — total symbols across all files
    """
    repo_path = Path(repo_path).resolve()

    file_outlines = []
    total_symbols = 0
    files_processed = 0

    for file_path in _walk_source_files(repo_path, max_files):
        try:
            outline = build_file_outline(file_path, repo_root=repo_path)
            if outline["symbol_count"] > 0:
                file_outlines.append(outline)
                total_symbols += outline["symbol_count"]
            files_processed += 1
        except Exception as e:
            log.warning("repo_outline_file_failed", file=str(file_path), error=str(e))

    return {
        "repo_path": str(repo_path),
        "files": file_outlines,
        "total_files": files_processed,
        "total_symbols": total_symbols,
    }


def _walk_source_files(root: Path, max_files: int):
    """Walk a directory and yield source files up to max_files."""
    from lgrep.discovery import FileDiscovery

    discovery = FileDiscovery(root)
    count = 0

    for file_path in discovery.find_files():
        if count >= max_files:
            log.info("repo_outline_max_files_reached", max=max_files)
            break
        # Only yield files with supported extensions
        if get_language_spec(file_path.suffix.lower()) is not None:
            yield file_path
            count += 1
