# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
