# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [3.0.1] - 2026-04-22

### Fixed

- **`search_semantic` response now includes `line_number`** — handler previously forwarded raw `SearchResult` dataclass fields (`start_line`, `end_line`, `match_type`) without mapping to the declared `SearchChunk` TypedDict which requires `line_number`. The handler now explicitly maps `line_number = start_line` while preserving range fields as optional fidelity keys.
- **`search_semantic` `total` now returns `len(results)`** — was always 0 because the handler read `result_dict.get("total", 0)` from a `SearchResults` dataclass that uses `total_chunks` (a corpus count), not `total`. Now correctly returns the number of results in this response.
- **`get_repo_outline` `files` declared as `list[FileOutline]`** — TypedDict previously declared `files: list[str]` but runtime already returned `list[dict]` with `file_path`, `symbols`, `symbol_count`. Declaration now matches runtime via new `FileOutline` TypedDict.

## [3.0.0] - 2026-04-20

### Upgrade from 2.x

`3.0.0` is a deliberate, atomic migration — there is no compat shim. Clients that invoke lgrep MCP tools must be ready for dict responses before upgrading.

1. **MCP response shape changed.** Tools used to return JSON strings. They now return TypedDict-shaped dicts directly. Example:
   - Before (`2.x`): `search_semantic(...)` → `'{"results": [...], "query": "..."}'`
   - After (`3.0.0`): `search_semantic(...)` → `{"results": [...], "query": "..."}`
   If your integration does `json.loads(response)` on tool output, remove that step after upgrading.
2. **Indexes are compatible.** Existing LanceDB semantic indexes and symbol indexes from 2.x do not need to be rebuilt. If a project returns unexpected errors, re-run `lgrep_index_semantic` or `lgrep_index_symbols_folder` to refresh.
3. **Log path moved.** Systemd deployments now log to `~/.cache/lgrep/lgrep.log` instead of `/tmp/lgrep.log`. The installer now creates `~/.cache/lgrep/` automatically; older service files should be regenerated with `lgrep install-opencode` to pick up the new path.
4. **Legacy `src/lgrep/storage.py` is gone.** If you imported from that module path, switch to the `lgrep.storage` package (public surface unchanged).

Rollback: if 3.0.0 integration is not feasible yet, pin to `lgrep==2.1.1`.

### Changed

- **BREAKING: MCP response contract is now structured dicts** — server tools now return TypedDict-shaped dictionaries instead of `json.dumps(...)` strings. This aligns the MCP wire contract with native object responses and removes double-serialization.
- **Server package split** — `src/lgrep/server.py` was split into `src/lgrep/server/` modules (`__init__.py`, `lifecycle.py`, `tools_semantic.py`, `tools_symbols.py`, `bootstrap.py`, `responses.py`) to isolate lifecycle, semantic tools, symbol tools, and response contracts.
- **Transport docs now frame stdio as local default** — README and packaged SKILL docs now say stdio is the default for single-session / single-user local setups, while preserving shared HTTP guidance for scale-up deployments.

### Added

- **Centralized MCP response contracts** — `src/lgrep/server/responses.py` defines `ToolError`, per-tool TypedDict response shapes, and shared timeout/error helpers.
- **JSONC installer support** — `install_opencode.py` now reads and writes `.jsonc` OpenCode configs with comment- and trailing-comma-safe parsing via `src/lgrep/_jsonc.py`.
- **Async query retry path** — semantic query embedding now uses `embed_query_async()` with `asyncio.sleep(...)` backoff, avoiding thread hops and blocking sleeps on interactive search retries.
- **Prune orphan semantic caches** — new `lgrep prune-orphans` CLI subcommand and `lgrep_prune_orphans` MCP tool inspect and (with `--execute`) delete orphaned semantic cache directories. Dry-run by default; `--execute` and `--dry-run` are mutually exclusive.
- **Orphan detection invariant** — `ChunkStore.__init__` now writes `project_meta.json` next to the LanceDB cache when a `project_path` is supplied, making the cache-hash → project-path mapping recoverable. Passing `project_path=None` intentionally skips the write (no hash-dir-as-project corruption).
- **Path-confinement + TOCTOU guards** — `prune_orphans` resolves each candidate and refuses any path outside the cache root or any symlinked cache entry before calling `shutil.rmtree`; refusals land in `failures[]` rather than as silent skips.
- **Grace window** — recently-modified caches are preserved for 1 hour by default so the pruner cannot race a live indexer. Configurable via `LGREP_PRUNE_MIN_AGE_S` (seconds; `0` disables). `missing_meta` and `project_path_enoent` reasons bypass the grace check.
- **Transport-aware MCP safety** — the MCP `prune_orphans` tool coerces `dry_run=True` on non-stdio transports (shared HTTP/SSE). Destructive prunes on shared deployments must use the CLI. Transport is plumbed via `LGREP_TRANSPORT` (set by `run_server`, stored on `LgrepContext.transport`).
- **Prune response contracts** — added `PruneOrphansResult`, `PruneOrphanEntry`, `PruneFailureEntry` TypedDicts in `lgrep.server.responses` so the new MCP surface shares the project's response-pattern convention.
- **Semantic cache lifecycle spec** — added `lgrepSemanticCacheLifecycle` capability (`v1.0.0`) with 6 requirements covering cache metadata, orphan detection, dry-run defaults, prune guards, MCP transport safety, and response contracts.

### Fixed

- **Installer log path is user-scoped** — systemd setup now uses `~/.cache/lgrep/lgrep.log` instead of `/tmp/lgrep.log`, and setup instructions create the cache directory explicitly.
- **Tool error handling is consistent** — error paths now return structured `{ "error": ... }` responses across semantic and symbol tools.
- **JSONC config install/uninstall edge cases** — installer now handles `//` comments, `/* ... */` blocks, trailing commas, and adversarial string literals in `.jsonc` files.
- **`ChunkStore` side-effect clarity** — constructor docstring now documents when `project_meta.json` is written and the opt-out via `project_path=None`.
- **Installer uninstall symlink safety** — `uninstall()` refuses to unlink `SKILL_PATH` / `INSTRUCTION_PATH` when they resolve through a parent-directory symlink into the package source tree, preventing accidental deletion of committed repo files during dev-workflow setups.
- **Live MCP structured errors** — semantic and symbol handlers now declare `ToolError` in their return annotations, so FastMCP returns structured `{ "error": ... }` payloads on live error paths instead of framework validation errors.

### Removed

- **Legacy duplicate storage module** — deleted `src/lgrep/storage.py`; all consumers now use the `lgrep.storage` package re-exporting from `storage/_chunk_store.py`.

### Added

- **Project URLs in pyproject.toml** — Homepage, Repository, and Changelog links now included in package metadata.
- **Project page link in README** — header nav bar and footer link to [sharperflow.com/projects/lgrep](https://sharperflow.com/projects/lgrep).

### Fixed

- **Flaky `test_has_disk_cache_check`** — monkeypatches `LGREP_CACHE_DIR` to isolate from global cache, preventing hash collisions with leftover data from prior pytest sessions.
- **Ruff formatting** — applied `ruff format` to 5 files that had drifted from the project style.
- Restored the SKILL.md `Tool Exposure Requirement` guidance so the packaged skill matches the routing-policy tests and current agent manifest requirements.
- Corrected install docs in `SKILL.md` and `README.md` so they describe all installer artifacts consistently: MCP entry, always-on instruction, and skill file.

## [2.1.1] - 2026-03-07

### Changed

- **Simplified SKILL.md** — removed redundant sections that duplicated the always-on instruction policy (`lgrep-tools.md`). The skill now focuses on being a concise decision matrix and tool reference rather than repeating canonical routing rules.
- **Removed SKILL.md manual config `instructions` example** — the manual setup snippet now shows only the `mcp` block, since the instruction file is handled by `lgrep install-opencode` or the user's own global config.

### Fixed

- **SKILL.md setup description** now correctly documents all three installer artifacts (MCP entry, instruction file, and skill) instead of omitting the instruction file.

## [2.1.0] - 2026-03-06

### Added

- **Packaged always-on instruction** — `instructions/lgrep-tools.md` now ships with the project and can be loaded through OpenCode's `instructions` array for reliable lgrep-first tool selection.
- **Installer policy wiring** — `lgrep install-opencode` now installs the always-on lgrep instruction and appends it to OpenCode config automatically.
- **Tool-routing policy tests** — added policy and installer regression coverage for lgrep-first routing and anti-pattern detection.

### Changed

- **OpenCode setup docs** now document always-on instruction wiring in README, example config, and installer output.
- **Skill positioning** — `SKILL.md` now acts as supplemental reference while always-loaded instructions carry the canonical routing policy.

### Added

- **`lgrep init-ignore` CLI command** — scaffolds a recommended `.lgrepignore` file in a project root.
- **Default `.lgrepignore` template** with practical excludes for dependencies, build artifacts, caches, generated files, and large fixtures.

### Changed

- Setup docs now explicitly show where to configure `VOYAGE_API_KEY` for both Vision/open-chad and raw OpenCode setups.

## [2.0.0] - 2026-03-05

### Added

- **Symbol engine** — 11 new MCP tools for exact symbol lookup, file/repo outlines, and text search. No API key required.
  - `lgrep_index_symbols_folder(path)` — index all symbols in a local folder via tree-sitter AST parsing
  - `lgrep_index_symbols_repo(repo, ref)` — index symbols from a GitHub repo via REST API (no git clone)
  - `lgrep_list_repos()` — list all indexed repositories
  - `lgrep_get_file_tree(path)` — get file tree respecting .gitignore
  - `lgrep_get_file_outline(path)` — get symbol outline for a single file (no index needed)
  - `lgrep_get_repo_outline(path)` — get symbol outline across an entire repository
  - `lgrep_search_symbols(query, path)` — case-insensitive substring symbol search
  - `lgrep_search_text(query, path)` — literal text search across source files
  - `lgrep_get_symbol(symbol_id, path)` — get full metadata + source for a symbol by ID
  - `lgrep_get_symbols(symbol_ids, path)` — batch symbol retrieval
  - `lgrep_invalidate_cache(path)` — remove symbol index, force re-index
- **Symbol IDs** — deterministic `file:kind:name` format (e.g. `src/auth.py:function:authenticate`). Stable across re-indexes, breaks only on rename.
- **tree-sitter-language-pack** dependency — 165+ languages, pre-built wheels, no compilation required.
- **pathspec** dependency — gitignore-style pattern matching for discovery security.
- **Security-hardened discovery** — path traversal validation, symlink escape detection, secret file detection (.env/.pem/credentials.*), binary file sniffing, per-file 1MB size cap, skip patterns for node_modules/vendor/dist/etc.
- **Token savings tracking** — `_meta` envelope on all symbol tool responses with `timing_ms` and `tokens_saved`.
- **CLI symbol commands** — `lgrep search-symbols <query> [path]` and `lgrep index-symbols [path]` one-shot wrappers.
- **Input validation** — empty query/path rejected with structured JSON errors; negative limits clamped to 1.

### Changed

- **Semantic tools renamed** (behavior unchanged, parameters unchanged):
  - `lgrep_search` → `lgrep_search_semantic`
  - `lgrep_index` → `lgrep_index_semantic`
  - `lgrep_status` → `lgrep_status_semantic`
  - `lgrep_watch_start` → `lgrep_watch_start_semantic`
  - `lgrep_watch_stop` → `lgrep_watch_stop_semantic`
- **CLI commands renamed**: `lgrep search` → `lgrep search-semantic`, `lgrep index` → `lgrep index-semantic`
- **`src/lgrep/storage/`** refactored from a single file to a package:
  - `storage.py` → `storage/_chunk_store.py` (semantic LanceDB storage, all public names re-exported)
  - `storage/__init__.py` re-exports all public names for backward compatibility
  - `storage/index_store.py` — new symbol index storage (JSON, atomic writes)
  - `storage/token_tracker.py` — new token savings ledger
- **Version bumped** to 2.0.0.
- **Test suite expanded** from 135 to 386+ tests.

### Migration from v1.x

Update any hardcoded tool names in prompts or scripts:

| v1.x | v2.0.0 |
|------|--------|
| `lgrep_search` | `lgrep_search_semantic` |
| `lgrep_index` | `lgrep_index_semantic` |
| `lgrep_status` | `lgrep_status_semantic` |
| `lgrep_watch_start` | `lgrep_watch_start_semantic` |
| `lgrep_watch_stop` | `lgrep_watch_stop_semantic` |

All parameters and response shapes are unchanged.

## [0.2.1] - 2026-02-13

### Fixed
- **Custom tool wrapper no longer throws ShellError.** Changed `Bun.$\`` to `Bun.$.nothrow\`` so CLI errors (missing API key, no index) return structured JSON to agents instead of crashing with `ShellError: Failed with exit code 1`.
- **CLI error responses include hints.** `search` and `index` commands now return a `"hint"` field in JSON errors with actionable remediation steps for agents.

### Added
- Regression tests ensuring the tool template uses `.nothrow` and contains no throwing shell calls.

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
