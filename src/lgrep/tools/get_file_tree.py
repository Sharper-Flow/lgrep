"""lgrep_get_file_tree tool implementation.

Returns the list of source files in a repository, respecting .gitignore.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from lgrep.tools._meta import error_response, make_meta

log = structlog.get_logger()


def get_file_tree(
    repo_path: str,
    max_files: int = 500,
) -> dict:
    """Get the file tree of a repository.

    Args:
        repo_path: Absolute path to the repository root
        max_files: Maximum number of files to return (default: 500)

    Returns:
        Dict with files list (relative paths), total_files, and _meta envelope
    """
    t0 = time.monotonic()
    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0),
        )

    from lgrep.discovery import FileDiscovery

    discovery = FileDiscovery(root)
    files = []
    for file_path in discovery.find_files():
        if len(files) >= max_files:
            break
        try:
            rel = str(file_path.relative_to(root))
        except ValueError:
            rel = str(file_path)
        files.append(rel)

    return {
        "repo_path": str(root.resolve()),
        "files": files,
        "total_files": len(files),
        "_meta": make_meta(t0),
    }
