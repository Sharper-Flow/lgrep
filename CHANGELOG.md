# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-02-06

### Added
- Initial release of `lgrep` MCP server.
- Semantic code search using Voyage Code 3 embeddings.
- Local vector storage via LanceDB.
- Hybrid search (Vector + FTS) with RRF reranking.
- AST-aware code chunking via Chonkie and tree-sitter.
- Automatic file discovery respecting `.gitignore` and `.lgrepignore`.
- Background file watcher for incremental indexing.
- Token usage tracking and cost threshold warnings.
- Graceful recovery from database corruption.
- SHA-256 hash-based skip optimization for indexing unchanged files.
- PEP 561 compliance with `py.typed`.
