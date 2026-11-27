"""lgrep_get_file_tree tool implementation.

Returns the list of source files in a repository, respecting .gitignore.
"""

from __future__ import annotations

import structlog

from lgrep.tools._paths import resolve_path

log = structlog.get_logger()


def get_file_tree(
    repo_path: str,
    max_files: int = 500,
) -> dict:
    """Get the file tree of a repository.

    Args:
        repo_path: Absolute path to the repository root, or to a file whose
            parent directory is the root
        max_files: Maximum number of files to return (default: 500)

    Returns:
        Dict with files list (relative paths), total_files, and repo_path,
        the root the tree was built from.
    """
    root = resolve_path(repo_path, "parent")
    if isinstance(root, dict):
        return root

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
    }
