"""lgrep_get_repo_outline tool implementation.

Returns the symbol outline across an entire repository.
"""

from __future__ import annotations

from lgrep.parser.hierarchy import build_repo_outline
from lgrep.tools._paths import resolve_path


def get_repo_outline(repo_path: str, max_files: int = 500) -> dict:
    """Get the symbol outline for an entire repository.

    Args:
        repo_path: Absolute path to the repository root, or to a file whose
            parent directory is the root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        Dict with repo_path (the root outlined), files list, total_files,
        and total_symbols
    """
    root = resolve_path(repo_path, "parent")
    if isinstance(root, dict):
        return root

    outline = build_repo_outline(root, max_files=max_files)
    return outline
