"""Resolve a ``path`` argument to the scope a tool works on.

A directory names itself under every scope, so a subdirectory of a checkout
keeps its own identity and never inherits the checkout's index. A regular
file names the file, its parent directory, or the toplevel of the Git working
tree containing it, according to what the tool needs. A missing path, a file
a tool cannot use, and a path that is neither a directory nor a regular file
are reported separately so the caller can correct its call.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Literal

from lgrep.tools._meta import error_response

Scope = Literal["file", "parent", "checkout", "directory"]

# Matches the git invocation timeout in _index_bootstrap.git_common_dir.
_GIT_TIMEOUT_S = 2.0


def _checkout_root(path: Path) -> Path | None:
    """Return the toplevel of the working tree containing file *path*, or None.

    ``--path-format=absolute`` requires Git >= 2.30 (January 2021); an older
    git, a missing git binary, or a file outside a working tree returns None.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--show-toplevel"],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    toplevel = result.stdout.strip()
    return Path(toplevel) if toplevel else None


def resolve_path(path: str, scope: Scope) -> Path | dict:
    """Return the resolved path a tool with *scope* works on, or an error dict.

    ``file`` keeps a file as the thing to search. ``parent`` maps a file to
    its directory. ``checkout`` maps a file to the toplevel of its Git
    working tree, so it shares that checkout's index. ``directory`` refuses a
    file, for commands that write into the directory they are given.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        # Includes an embedded NUL byte, which no filesystem path can hold.
        return error_response(f"Path does not exist: {path}")
    if not resolved.exists():
        return error_response(f"Path does not exist: {path}")
    if resolved.is_dir():
        return resolved
    if not resolved.is_file():
        return error_response(f"Path is not a directory or a regular file: {path}")
    if scope == "file":
        return resolved
    if scope == "parent":
        return resolved.parent
    if scope == "directory":
        return error_response(f"Path is a file; pass a directory: {path}")
    root = _checkout_root(resolved)
    if root is None:
        return error_response(
            f"Path is a file outside a git checkout; pass a repository root: {path}"
        )
    return root


async def resolve_path_async(path: str, scope: Scope) -> Path | dict:
    """Run :func:`resolve_path` off the event loop for a server handler."""
    return await asyncio.to_thread(resolve_path, path, scope)


def resolve_repo(repo_path: str) -> str | dict:
    """Return the key an index is stored under for *repo_path*, or an error dict.

    A ``github:`` key is already such a key. A local path names its Git
    checkout root, so a file shares its checkout's index.
    """
    if repo_path.startswith("github:"):
        return repo_path
    resolved = resolve_path(repo_path, "checkout")
    return str(resolved) if isinstance(resolved, Path) else resolved
