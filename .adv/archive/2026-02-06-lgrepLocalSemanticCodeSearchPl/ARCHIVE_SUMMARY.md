# Archive: lgrep: Local semantic code search plugin for OpenCode TUI - replaces mgrep's cloud backend with LanceDB for 100% local, 5-10x faster semantic search

**Change ID:** lgrepLocalSemanticCodeSearchPl
**Archived:** 2026-02-06T17:12:16.988Z
**Created:** 2026-02-05T05:29:40.507Z

## Tasks Completed

- ⏭️ Initialize lgrep project structure in ~/dev/opencode-lgrep with Cargo workspace, pyproject.toml for plugin, and README
- ⏭️ Copy and adapt mgrep plugin structure (hooks/hook.json, hooks/*.py, skills/SKILL.md) as template
- ⏭️ Implement lgrep CLI scaffolding with clap: subcommands for search, --index, --status, --watch, --json
- ⏭️ Integrate LanceDB: create schema, connect to per-project database in ~/.cache/lgrep/
- ✅ Implement file discovery: walk directory tree respecting .gitignore and .lgrepignore
- ⏭️ Implement chunking: split files into ~500 token chunks with 50 token overlap, preserve line numbers
- ⏭️ Integrate FastEmbed: load Jina Code V2 model, batch embed chunks
- ⏭️ Implement hybrid search: combine vector similarity with BM25 keyword matching via LanceDB
- ⏭️ Implement --index command: full index build with progress reporting
- ⏭️ Implement search command: query index, format results as file:line: context
- ⏭️ Implement --watch command: file watcher daemon with incremental index updates
- ⏭️ Implement --status command: show index stats (files, chunks, last updated)
- ⏭️ Create OpenCode plugin: hooks/hook.json with SessionStart/SessionEnd triggers
- ⏭️ Create OpenCode plugin: hooks/lgrep_watch.py to spawn watcher on session start
- ⏭️ Create OpenCode plugin: hooks/lgrep_watch_kill.py to terminate watcher on session end
- ✅ Create OpenCode plugin: skills/lgrep/SKILL.md with agent instructions for semantic search
- ✅ Add tests: unit tests for chunking, file discovery, and search result formatting
- ✅ Add tests: integration tests for index build and search on sample codebase
- ✅ End-to-end validation: test lgrep on ~/dev/pokeedge with semantic queries
- ✅ Documentation: README with installation, usage, and OpenCode plugin setup
- ⏭️ RESEARCH: Evaluate sqlite-vec + rusqlite performance for 75k chunks with hybrid search (vector + FTS5 + RRF)
- ⏭️ RESEARCH: Benchmark fastembed-rs CPU performance for Jina Code V2 on sample codebase
- ⏭️ RESEARCH: Prototype tree-sitter AST chunking for Rust/Python/TypeScript with fallback to fixed-size
- ✅ Update proposal.md: Replace LanceDB with sqlite-vec + FTS5, increase chunk overlap to 100-150 tokens
- ✅ Initialize lgrep Python project with pyproject.toml, src/lgrep/ package structure, and dependencies (lancedb, fastembed, mcp, watchdog)
- ✅ Create MCP server skeleton: src/lgrep/server.py with stdio transport, tool registration scaffold
- ✅ Implement MCP tool: lgrep_search(query, limit, hybrid) → SearchResults with file, line, content, score
- ✅ Implement MCP tool: lgrep_index(path) → IndexStatus with file count, chunk count, duration
- ✅ Implement MCP tool: lgrep_status() → {files, chunks, last_updated, watching}
- ✅ Implement MCP tools: lgrep_watch_start(path), lgrep_watch_stop() for background indexing control
- ✅ Integrate LanceDB Python: create schema (chunks table), connect to per-project database in ~/.cache/lgrep/
- ✅ Implement hybrid search: vector similarity + BM25 via LanceDB native FTS with RRFReranker
- ⏭️ Integrate FastEmbed Python: load Jina Code V2 model once at server startup, batch embed chunks
- ✅ Implement file watcher: watchdog integration as async task, debounce 100ms, incremental re-indexing
- ✅ Add concurrent query handling: ensure 3+ agents can query simultaneously without blocking
- ✅ Create OpenCode MCP config example: opencode.json snippet for lgrep server registration
- ✅ Integrate Voyage AI: implement embed() and embed_query() using voyage-code-3 model with batching and rate limiting
- ✅ Add Voyage API key configuration: environment variable VOYAGE_API_KEY, validation on startup
- ✅ Update LanceDB schema: change embedding dimensions from 768 (Jina) to 1024 (Voyage Code 3)
- ✅ Integrate Chonkie CodeChunker for AST-aware code chunking (pip install chonkie[code])
- ✅ Document OpenAI text-embedding-3-large as fallback embedding option in proposal
- ✅ Implement Voyage API error handling: retry with exponential backoff, rate limit detection (429), timeout handling (10s), network error recovery
- ✅ Implement Voyage API batching: queue embed requests, batch up to 128 texts per call, respect 300 req/min rate limit
- ✅ Setup structured logging with structlog: configure JSON output, request correlation IDs, timing decorators
- ✅ Add token usage tracking: count tokens per embed call, accumulate daily/monthly totals, warn at $5/$10 thresholds
- ✅ Fix SQL injection in ChunkStore.delete_by_file: file_path is interpolated into f-string SQL predicate (storage.py:182). Escape single quotes or use parameterized deletion to prevent breakage/injection from paths containing quotes.
- ✅ Fix blocking time.sleep() in VoyageEmbedder retry loops (embeddings.py:115,164): either make embed methods async with asyncio.sleep(), or ensure server.py runs embedding calls in an executor so the event loop is not blocked during retries (breaks concurrent query support).
- ✅ Remove or implement CLI stub commands: cli.py index/status commands print "not yet implemented". Either wire them to the Indexer/ChunkStore directly (for standalone use) or remove them to avoid dead code confusing users.
- ✅ Implement token cost threshold warnings: tk-mrH2LkSq claimed $5/$10 warnings but only cumulative counter exists. Add cost calculation (Voyage Code 3 pricing), threshold comparison, and structured log warnings. Optionally persist token counts across restarts via a JSON file in the cache dir.
- ✅ Add graceful LanceDB corruption recovery: wrap ChunkStore.__init__ and table property with try/except that detects corruption, logs a warning, and offers to rebuild (clear + re-index) rather than crashing the MCP server.
- ✅ Filter watcher file events by supported code extensions: IndexingHandler triggers re-index on ANY file change (images, binaries, .lock files). Add a check against LANGUAGE_MAP extensions before scheduling indexing to avoid wasting Voyage API tokens on non-code files.
- ✅ Add test for VoyageEmbedder retry/backoff behavior: mock transient failures (first N calls raise, then succeed) and verify retry count, exponential delay pattern, and final success. Also test permanent failure after MAX_RETRIES.
- ✅ Add server tool error path tests: test lgrep_index with invalid path, missing API key, and indexing failure. Test lgrep_search with no index. Test lgrep_watch_start/stop edge cases.
- ✅ Optimize get_indexed_files to use SELECT DISTINCT query instead of loading entire table into memory via to_arrow(). For 75k chunks this loads all data unnecessarily. Use a LanceDB filter or SQL query to get unique file_path values only.
- ✅ Add py.typed marker file to src/lgrep/ for PEP 561 type checking support.

## Specs Modified

