"""lgrep - Dual-engine code intelligence MCP server.

Semantic engine: Voyage Code 3 embeddings with local LanceDB storage for 92%
retrieval quality at ~$3/month cost.

Symbol engine: tree-sitter AST parsing for exact symbol lookup, file outlines,
and structural code navigation across 165+ languages.
"""

import importlib.metadata

# Hatch VCS derives the version from the release tag at build time. The runtime
# source of truth is the installed distribution metadata; fall back to a
# generated version file (if built with the VCS hook) or a safe default when
# running from source without an editable install.
try:
    __version__ = importlib.metadata.version("lgrep")
except importlib.metadata.PackageNotFoundError:
    try:
        from lgrep._version import __version__  # type: ignore[import-not-found, no-redef]
    except ImportError:
        __version__ = "0.0.0"
