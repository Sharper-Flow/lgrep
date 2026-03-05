"""lgrep_search_text tool implementation.

Performs literal text search across source files in a repository.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from lgrep.tools._meta import error_response, make_meta

log = structlog.get_logger()


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
        Dict with results list (file_path, line_number, line) and _meta envelope.
        Returns error dict if the path does not exist.
    """
    t0 = time.monotonic()

    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty", _meta=make_meta(t0))

    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0),
        )

    from lgrep.discovery import FileDiscovery

    discovery = FileDiscovery(root)
    results = []
    search_query = query if case_sensitive else query.lower()

    for file_path in discovery.find_files():
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
        "_meta": make_meta(t0),
    }
