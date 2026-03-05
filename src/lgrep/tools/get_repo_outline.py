"""lgrep_get_repo_outline tool implementation.

Returns the symbol outline across an entire repository.
"""

from __future__ import annotations

import time
from pathlib import Path

from lgrep.parser.hierarchy import build_repo_outline
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta


def get_repo_outline(repo_path: str, max_files: int = 500) -> dict:
    """Get the symbol outline for an entire repository.

    Args:
        repo_path: Absolute path to the repository root
        max_files: Maximum number of files to process (default: 500)

    Returns:
        Dict with repo_path, files list, total_files, total_symbols, and _meta envelope
    """
    t0 = time.monotonic()
    root = Path(repo_path)

    if not root.exists() or not root.is_dir():
        return error_response(
            f"Path does not exist or is not a directory: {repo_path}",
            _meta=make_meta(t0),
        )

    outline = build_repo_outline(root, max_files=max_files)
    tokens_saved = estimate_savings(outline["total_symbols"])
    outline["_meta"] = make_meta(t0, tokens_saved=tokens_saved)
    return outline
