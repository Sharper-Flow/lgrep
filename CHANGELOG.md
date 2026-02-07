# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-02-07

### Added
- **Auto-index on first search.** `lgrep_search` on a cold project triggers indexing automatically — no manual `lgrep_index` call required.
- **Single-flight concurrency.** Concurrent searches on the same cold project share a single indexing operation via `asyncio.Event`, preventing duplicate work.
- **Disk-cache auto-load.** On server restart, previously indexed projects are loaded from `~/.cache/lgrep/` on first search — no re-indexing needed.
- **`LGREP_WARM_PATHS` env var.** Colon-separated project paths to pre-load from disk cache at server startup, eliminating cold-start latency.
- **Tool-choice decision matrix.** SKILL.md and README now include explicit policy for when agents should use `lgrep_search` vs `Grep` vs `Read`.
- **Streamable HTTP security docs.** Documented localhost binding, authentication, CORS, and explicit opt-in for `--transport streamable-http`.
- **`has_disk_cache()` utility.** Checks for cached indexes on disk without opening the database.

### Changed
- Default transport is now explicitly `stdio` (was implicit before).
- Error messages for unindexed projects improved (now says "does not exist" instead of suggesting `lgrep_index`).
- Test suite expanded from 81 to 135 tests.

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
