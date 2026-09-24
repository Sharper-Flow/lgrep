"""lgrep_get_file_outline tool implementation.

Returns the symbol outline (functions, classes, methods) for a single file.
"""

from __future__ import annotations

from pathlib import Path

from lgrep.parser.hierarchy import build_file_outline
from lgrep.tools._meta import error_response


def get_file_outline(file_path: str, repo_root: str | None = None) -> dict:
    """Get the symbol outline for a single source file.

    Args:
        file_path: Absolute path to the source file
        repo_root: Optional repo root for relative path computation in symbol IDs

    Returns:
        Dict with file_path, symbols list, and symbol_count
    """
    path = Path(file_path)

    if not path.exists():
        return error_response(
            f"File does not exist: {file_path}",
        )
    if not path.is_file():
        return error_response(
            f"Path is not a file: {file_path}",
        )

    root = Path(repo_root) if repo_root else None
    outline = build_file_outline(path, repo_root=root)

    return outline
