"""lgrep - Dual-engine code intelligence MCP server.

Semantic engine: Voyage Code 3 embeddings with local LanceDB storage for 92%
retrieval quality at ~$3/month cost.

Symbol engine: tree-sitter AST parsing for exact symbol lookup, file outlines,
and structural code navigation across 165+ languages.
"""

from __future__ import annotations

import importlib.metadata
import subprocess
from pathlib import Path


def _version_from_vcs() -> str | None:
    """Return the version derived from the nearest reachable release tag.

    This keeps a source checkout from reporting a stale, host-installed
    distribution version when ``PYTHONPATH`` points at the worktree ``src/``
    directory instead of an editable or site-packages installation.
    """
    here = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=here,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    tag = result.stdout.strip()
    if tag.startswith("v"):
        return tag[1:]
    return tag


def _version_from_metadata() -> str | None:
    try:
        return importlib.metadata.version("lgrep")
    except importlib.metadata.PackageNotFoundError:
        return None


def _resolve_version() -> str:
    # 1. Build-time generated version file (wheels/sdists built with Hatch VCS).
    try:
        from lgrep._version import __version__ as _vcs_version  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        return _vcs_version

    # 2. Source checkout: derive directly from the release tag so that running
    #    with PYTHONPATH=src does not pick up an unrelated host installation.
    source_version = _version_from_vcs()
    if source_version is not None:
        return source_version

    # 3. Installed distribution metadata (normal runtime fallback).
    metadata_version = _version_from_metadata()
    if metadata_version is not None:
        return metadata_version

    return "0.0.0"


__version__ = _resolve_version()
