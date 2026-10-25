"""lgrep_search_text tool implementation.

Performs literal text search across source files in a repository.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.tools._meta import error_response

if TYPE_CHECKING:
    from collections.abc import Iterator

log = structlog.get_logger()

# Agents call search_text in bursts: most calls on a repository follow another
# within seconds. A complete file list is reused for this long, so files
# created or deleted inside the window are seen on the next walk.
_FILE_LIST_TTL_S = 5.0
_file_lists: dict[Path, tuple[float, list[Path]]] = {}
_file_lists_lock = threading.Lock()


def _source_files(root: Path) -> Iterator[Path]:
    """Yield the repository's searchable files, from a fresh list if one exists.

    A walk is cached only when the caller consumes all of it, so a search
    that stops at ``max_results`` keeps its early exit.
    """
    from lgrep.discovery import FileDiscovery

    key = root.resolve()
    now = time.monotonic()
    with _file_lists_lock:
        cached = _file_lists.get(key)
    if cached is not None and now - cached[0] < _FILE_LIST_TTL_S:
        yield from cached[1]
        return
    files: list[Path] = []
    for file_path in FileDiscovery(root).find_files():
        files.append(file_path)
        yield file_path
    with _file_lists_lock:
        for stale in [k for k, (at, _) in _file_lists.items() if now - at >= _FILE_LIST_TTL_S]:
            del _file_lists[stale]
        _file_lists[key] = (now, files)


def search_text(
    query: str,
    repo_path: str,
    max_results: int = 50,
    case_sensitive: bool = False,
) -> dict:
    """Search for literal text across all source files in a repository.

    Args:
        query: Text to search for
        repo_path: Absolute path to the repository root
        max_results: Maximum number of results to return (default: 50)
        case_sensitive: Whether to perform case-sensitive matching (default: False)

    Returns:
        Dict with results list (file_path, line_number, line).
        Returns error dict if the path does not exist.
    """
    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty")

    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
        )

    results = []
    search_query = query if case_sensitive else query.lower()

    for file_path in _source_files(root):
        if len(results) >= max_results:
            break
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_num, line in enumerate(text.splitlines(), start=1):
            compare = line if case_sensitive else line.lower()
            if search_query in compare:
                try:
                    rel_path = str(file_path.relative_to(root))
                except ValueError:
                    rel_path = str(file_path)
                results.append(
                    {
                        "file_path": rel_path,
                        "line_number": line_num,
                        "line": line.rstrip(),
                    }
                )
                if len(results) >= max_results:
                    break

    return {
        "results": results,
        "total_matches": len(results),
    }
