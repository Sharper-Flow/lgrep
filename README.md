<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lgrep-header-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/lgrep-header-light.svg">
    <img alt="lgrep — local-first code intelligence" src="docs/assets/lgrep-header-light.svg" width="640">
  </picture>
</p>

<p align="center">
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11+-3776ab?logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT" /></a>
</p>

`lgrep` is a local-first code-intelligence MCP server for AI coding agents such as [OpenCode](https://github.com/opencode-ai/opencode). Agents search by meaning when they do not know the symbol yet, search by symbol when they do, and inspect file and repo structure before opening code — reusing one warm local server across sessions instead of repeating `glob`/`grep`/random-file-read cycles. Working trees stay on disk; only short semantic queries and indexing payloads reach the Voyage API, and symbol lookup is fully local with no API key.

## How it works

Two engines behind one MCP server:

- **Semantic engine** — natural-language code search: Voyage Code 4 embeddings, AST-aware chunking, local LanceDB storage, hybrid retrieval with reranking. 30+ languages, text fallback where needed.
- **Symbol engine** — exact structure via tree-sitter parsing into a local JSON index: symbol search, file/repo outlines, candidate references, and source retrieval with no API call. 165+ languages via tree-sitter-language-pack.

Staleness is handled automatically: each semantic search runs a staleness pre-flight and refreshes in the background when the index has drifted. Details in [docs/operations.md](docs/operations.md).

## Installation

Requirements: Python 3.11+, and a [Voyage API key](https://dash.voyageai.com/) for the semantic engine only.

```bash
# From GitHub
pip install git+https://github.com/Sharper-Flow/lgrep.git

# Or from a source checkout
git clone https://github.com/Sharper-Flow/lgrep.git
cd lgrep && pip install .
```

## OpenCode setup

**stdio is the local default** for single-session, single-user setups — OpenCode starts lgrep itself and no server process is needed. For multi-session deployments, use the shared HTTP server below.

### 1. Run the installer

```bash
lgrep install-opencode
```

The installer is idempotent. It:

- creates `~/.cache/lgrep/` for indexes and logs
- copies the packaged `instructions/lgrep-tools.md` and `skills/lgrep/SKILL.md` into `~/.config/opencode/`
- appends the instruction file to the `instructions` array so agents prefer `lgrep` first
- adds a `type: "remote"` MCP entry pointing at `http://localhost:6285/mcp` (the shared HTTP server)
- edits `~/.config/opencode/opencode.json`, or `opencode.jsonc` when only that file exists; it keeps every config value but rewrites the file as plain JSON, so comments in `opencode.jsonc` are removed
- prints systemd user-service and manual-daemon instructions for the shared server

`lgrep uninstall-opencode` removes the MCP entry, the instruction entry, and the skill file.

### 2. Choose a transport

For stdio (local default), replace the `mcp.lgrep` entry with:

```json
{ "mcp": { "lgrep": { "type": "local", "command": ["lgrep"], "enabled": true } } }
```

OpenCode requires `command` for a `local` entry ([OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/)). Put `VOYAGE_API_KEY` in its `environment` object, or in the environment OpenCode starts from.

For the **shared HTTP** server, keep the installer's `remote` entry and start one warm server that handles every session:

```bash
VOYAGE_API_KEY=your-key \
LGREP_WARM_PATHS=/path/to/project-a:/path/to/project-b \
lgrep --transport streamable-http --host 127.0.0.1 --port 6285
```

```json
{ "mcp": { "lgrep": { "type": "remote", "url": "http://localhost:6285/mcp", "enabled": true } } }
```

Optional: `lgrep init-ignore /path/to/project` scaffolds a `.lgrepignore` (in addition to the always-respected `.gitignore`). Cache maintenance, worktree dedup, and daemon tuning live in [docs/operations.md](docs/operations.md).

The active agent profile must also expose the lgrep tools in its manifest: a profile that allows only `read`/`glob`/`grep` cannot choose lgrep even when the server is configured.

## First use

1. Ask an intent question: `search_semantic(query="authentication flow", path="/path/to/project")` — cold projects auto-index on first search.
2. Inspect structure: `get_file_outline(path=...)` or `get_repo_outline(path=...)`.
3. Retrieve exact symbols: `search_symbols(...)`, then `get_symbol(symbol_id="src/auth.py:function:authenticate", path=...)`.
4. Find bounded candidate usages: `search_references(query="authenticate", path=...)`.

Symbol IDs are deterministic `file_path:kind:name` strings, for example `src/auth.py:class:AuthManager` or `src/auth.py:method:login`.

## Tool reference

The server registers these bare names, and every MCP client sees them as registered. OpenCode displays them with the server-key prefix, so with an MCP entry named `lgrep`, `search_semantic` appears as `lgrep_search_semantic`. Only `lgrep_diagnostics` carries the prefix in its registered name.

### Semantic tools

| Tool | Purpose |
|---|---|
| `search_semantic(query, path, limit=10, hybrid=true, include_content=false)` | Search code by natural-language meaning |
| `index_semantic(path)` | Build or refresh the semantic index |
| `status_semantic(path="")` | Index/watcher status; omit `path` for all loaded projects |
| `watch_start_semantic(path)` | Start background re-indexing on file changes |
| `watch_stop_semantic(path="")` | Stop one watcher or all |

### Symbol tools

| Tool | Purpose |
|---|---|
| `index_symbols_folder(path, max_files=500, incremental=true)` | Index symbols in a local folder |
| `index_symbols_repo(repo, ref="HEAD", max_files=500, github_token=None)` | Index symbols from a GitHub repo via API, no clone |
| `list_repos()` | List indexed symbol repos |
| `get_file_tree(path, max_files=500)` | Repo file tree with ignore rules applied |
| `get_file_outline(path, repo_root=None)` | Symbol outline for one file |
| `get_repo_outline(path, max_files=500)` | Symbol outlines across a repo |
| `search_symbols(query, path, limit=20, kind=None)` | Search symbols by name (case-insensitive substring) |
| `search_text(query, path, limit=50, case_sensitive=false)` | Search literal text |
| `search_references(query, path, limit=20, usage_filter="production_first", kind=None)` | Bounded candidate usages; not compiler-exhaustive |
| `get_symbol(symbol_id, path)` | One symbol's metadata and source |
| `get_symbols(symbol_ids, path)` | Batch symbol retrieval |
| `invalidate_cache(path)` | Drop one repo's symbol index; needs the destructive-MCP grant |

### Maintenance and diagnostics

| Tool | Purpose |
|---|---|
| `prune_orphans(dry_run=true)` | Preview (or delete) orphan semantic cache dirs |
| `prune_symbols(dry_run=true)` | Preview (or delete) stale symbol-store indexes |
| `invalidate_worktree_cache(paths)` | Remove a worktree's cache alias and overlay rows |
| `lgrep_diagnostics()` | Read-only daemon snapshot: PID, uptime, projects, jobs |

Prune and invalidate tools refuse to delete without the server-side `LGREP_ALLOW_DESTRUCTIVE_MCP` grant; the pruners also skip projects loaded in the running server.

## CLI reference

| Command | Purpose |
|---|---|
| `lgrep` | Start the MCP server: `--transport {stdio,streamable-http}` (default `stdio`), `--host` (default `127.0.0.1`), `--port` (default `6285`) |
| `lgrep --version` | Print the version |
| `lgrep search-semantic <query> [path]` | One-shot semantic search; `-m/--limit N`, `--no-hybrid` |
| `lgrep index-semantic [path]` | One-shot semantic index; `--chunk-size N` |
| `lgrep search-symbols <query> [path]` | One-shot symbol search; `-m/--limit N`, `--storage-dir DIR` |
| `lgrep index-symbols [path]` | Index symbols; `--storage-dir DIR`, `--max-files N` |
| `lgrep init-ignore [path]` | Create a recommended `.lgrepignore`; `--force` |
| `lgrep prune-orphans` | Inspect or delete orphan semantic caches; `--execute`, `--dry-run`, `--cache-dir DIR` |
| `lgrep prune-symbols` | Inspect or delete stale symbol indexes; `--execute`, `--dry-run`, `--storage-dir DIR` |
| `lgrep gc` | Combined GC: orphans + worktree aliases + symbol indexes; `--execute`, `--dry-run`, `--cache-dir DIR`, `--symbols-dir DIR` |
| `lgrep remove <path>` | Show on-disk index info for a project |
| `lgrep install-opencode` | Install into OpenCode (MCP entry + instruction + skill) |
| `lgrep uninstall-opencode` | Remove lgrep from OpenCode |

`search` and `index` are aliases for `search-semantic` and `index-semantic`; `init-lgrepignore` aliases `init-ignore`. Prune commands are dry-run by default and `--execute`/`--dry-run` are mutually exclusive.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `VOYAGE_API_KEY` | none | Required for semantic search; the symbol engine works without it |
| `LGREP_LOG_LEVEL` | `INFO` | Log verbosity |
| `LGREP_LOG_FILE` | unset | Opt-in rotating JSON log file; see [operations](docs/operations.md#notes-on-selected-environment-variables) |
| `LGREP_CACHE_DIR` | `~/.cache/lgrep` | Semantic cache directory |
| `LGREP_SYMBOLS_DIR` | `~/.cache/lgrep/symbols` | Directory that `prune-symbols` and the `gc` symbol pass scan; indexing and symbol queries always use the default directory |
| `LGREP_WARM_PATHS` | none | Colon-separated projects to warm on startup |
| `LGREP_AUTO_WARM_DISK` | `true` | Auto-load discoverable disk caches at startup when no warm paths are set; set `false` on large shared machines |
| `LGREP_AUTO_WATCH` | `false` | Auto-start file watchers for warmed projects |
| `LGREP_TOOL_TIMEOUT_S` | `45` | Per-tool server-side timeout (seconds) |
| `LGREP_STALENESS_DEADLINE_S` | `4.0` | Bound on the per-search staleness check |
| `LGREP_ENSURE_BUDGET_S` | `8.0` | Budget for a worktree search to make its trunk base index current; `0` always defers to the background |
| `LGREP_INDEX_MAX_WALL_S` | `60.0` | Wall-clock budget per indexing window |
| `LGREP_AUTO_REFRESH` | `1` | `0` opts out of the symbol-index freshness gate that refreshes a stale index before `search_symbols` answers |
| `LGREP_GITHUB_TOKEN` | unset | Token for `index_symbols_repo` when the call passes none |
| `GITHUB_TOKEN` | unset | Fallback when `LGREP_GITHUB_TOKEN` is unset; an explicit argument wins over both |
| `LGREP_WORKER_MAX_THREADS` | `4` | Query-lane worker threads for supervised blocking jobs |
| `LGREP_BUILD_MAX_THREADS` | `1` | Build-lane threads: index windows, re-indexes, prune sweeps, remote indexing |
| `LGREP_PRUNE_MIN_AGE_S` | `3600` | Grace window (seconds) before pruning; `0` disables grace |
| `LGREP_WORKTREE_DEDUP` | unset | When set, git worktrees of one repo share one semantic cache |
| `LGREP_ALLOW_DESTRUCTIVE_MCP` | unset | `true`/`1`/`yes` lets MCP prune/invalidate tools delete; keep unset on any shared server |

## Transport and security

`lgrep` supports `stdio` and `streamable-http`. Use stdio for the local single-session default; use the shared HTTP transport only when you intentionally want one shared local daemon.

- The HTTP server binds `127.0.0.1` by default; there is no built-in authentication layer.
- lgrep sets no CORS headers, and browser-based clients should not connect directly to the streamable-HTTP endpoint.
- Behind a proxy, enforce your own authentication and origin controls there.
- Binding `0.0.0.0` is a non-default, explicit opt-in; do not do it without a reverse proxy or firewall.

## Troubleshooting

- **`VOYAGE_API_KEY` not set** — set it in the MCP server environment; the symbol engine still works without it.
- **Slow first semantic index** — the first run embeds the whole project; later runs skip unchanged files by content hash.
- **`Repository not indexed`** — symbol tools auto-build the index on first query for local git checkouts; subdirectory queries need an explicit `index_symbols_folder`. Details in [operations](docs/operations.md#freshness-and-index-budgets).
- **Stale semantic results** — searches serve the current index and refresh in the background automatically; see [operations](docs/operations.md#freshness-and-index-budgets).
- **Investigating high CPU on a shared daemon** — call `lgrep_diagnostics` for PID, worker limit, and active jobs; see [operations](docs/operations.md#shared-daemon-tuning-vision--opencode).
- **Native dependency build issues** — prebuilt wheels usually work; otherwise install a compiler toolchain.

Since `3.0.0` every MCP tool returns structured dicts instead of JSON strings; upgrading from `2.x`? See [Upgrade from 2.x](CHANGELOG.md#upgrade-from-2x).

## Development

```bash
git clone https://github.com/Sharper-Flow/lgrep.git
cd lgrep
pip install -e ".[dev]"
pytest -v
```

## License

MIT — see `LICENSE`.
