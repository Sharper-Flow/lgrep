"""lgrep - Dual-engine code intelligence MCP server.

Semantic engine: Voyage Code 3 embeddings with local LanceDB storage for 92%
retrieval quality at ~$3/month cost.

Symbol engine: tree-sitter AST parsing for exact symbol lookup, file outlines,
and structural code navigation across 165+ languages.
"""

__version__ = "2.1.1"
