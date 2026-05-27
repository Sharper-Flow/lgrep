---
name: lgrep
description: "PREFERRED: Dual-engine code intelligence (semantic search + symbol lookup). Use INSTEAD of built-in Grep/Glob for code exploration, concept search, and finding implementations by intent. Semantic engine: 92% retrieval quality. Symbol engine: exact function/class lookup, no API key needed."
keywords: ["lgrep", "semantic-search", "symbol-search", "code-exploration", "codebase-navigation", "intent-search"]
license: MIT
metadata:
  priority: high
  replaces: grep glob
---

## CRITICAL: Tool Priority

lgrep provides **two complementary search engines**:

1. **Semantic engine** (`lgrep_search_semantic`) — understands code *meaning*. Uses Voyage Code 3 embeddings (92% retrieval quality) with local LanceDB storage.
2. **Symbol engine** (`lgrep_search_symbols`, `lgrep_get_file_outline`, etc.) — understands code *structure*. Exact function/class/method lookup via tree-sitter AST. No API key needed.

Use this first-action policy:

- For intent-based discovery, **call `lgrep_search_semantic` first**.
- For exact symbol lookup by name, **call `lgrep_search_symbols`** (after indexing).
- For file structure overview, **call `lgrep_get_file_outline`** (no index needed).
- For exact identifier/regex lookups, use built-in `Grep` first.
- For known-file inspection, read the file directly.

### Decision Matrix

| Use case | Best tool | Notes |
|---|---|---|
| **Intent search** ("how is auth handled?") | `lgrep_search_semantic` | Semantic retrieval finds meaning |
| **Find function by name** ("find authenticate") | `lgrep_search_symbols` | Exact symbol lookup, fast |
| **File structure** ("what's in auth.py?") | `lgrep_get_file_outline` | No index needed |
| **Repo structure** ("what's in this codebase?") | `lgrep_get_repo_outline` | Full symbol map |
| **Exact text/identifier** ("find verifyToken") | `lgrep_search_text` or `Grep` | Literal matching |
| **Get symbol source** (by ID) | `lgrep_get_symbol` | Byte-precise retrieval |
| **Known-file review** | `Read` | Direct inspection |

### Priority examples

- **Use `lgrep_search_semantic` first:** "where is auth enforced between API and service layer?"
- **Use `lgrep_search_symbols` first:** "find the `authenticate` function"
- **Use `lgrep_get_file_outline` first:** "what functions are in `src/auth.py`?"
- **Use `Grep` first:** "find all references to `verifyToken`"
- **Use file read first:** "open `src/auth/jwt.ts` and explain line 42"

### Tool Exposure Requirement

Instruction text alone is not enough. The active agent or sub-agent also needs
the `lgrep_*` tool definitions in its tool manifest.

- If the manifest omits `lgrep_search_semantic`, `lgrep_search_symbols`, or
  related `lgrep_*` tools, the model cannot follow this routing policy and will
  fall back to `glob`/`grep`/`read`.
- In agent frontmatter, explicitly allow the tools you expect to use (for
  example `lgrep_search_semantic: true`, `lgrep_search_symbols: true`,
  `lgrep_get_file_outline: true`, `lgrep_search_text: true`).
- Do not assume `mcp.lgrep` in `opencode.json` is enough for every agent
  profile; agent-level tool allowlists can still hide the tools.

## Setup

**stdio is the local default** for single-session / single-user setups. For shared deployments, use the HTTP transport option.

**API key (semantic engine only):**
- Semantic tools (`lgrep_search_semantic`, `lgrep_index_semantic`) require `VOYAGE_API_KEY`.
- Symbol tools work without any API key.
- If using **Vision / open-chad**: set `VOYAGE_API_KEY` under `lgrep.env` in `~/.config/vision/servers.yaml`.
- If using **raw OpenCode MCP config**: set `VOYAGE_API_KEY` in `mcp.lgrep.env` in `~/.config/opencode/opencode.json`.

**Vision / OpenCode tuning for agent-heavy worktrees:**
```yaml
lgrep:
  env:
    VOYAGE_API_KEY: "${VOYAGE_API_KEY}"
    LGREP_WORKTREE_DEDUP: "1"
    LGREP_WARM_PATHS: "/abs/path/to/primary-repo:/abs/path/to/tooling-repo"
    LGREP_AUTO_WARM_DISK: "false"
    LGREP_TOOL_TIMEOUT_S: "8"
    LGREP_WORKER_MAX_THREADS: "4"
```

Use explicit `LGREP_WARM_PATHS` for repos agents actively use. Do not warm every cached repo by default on large multi-repo machines. Set `LGREP_TOOL_TIMEOUT_S` below the MCP proxy/client deadline so agents receive structured lgrep errors instead of transport-level deadline failures. Keep `LGREP_WORKER_MAX_THREADS` small for shared daemons so concurrent sessions cannot create unbounded blocking work.

If a Vision/shared HTTP daemon shows high CPU or many threads, call `lgrep_diagnostics` first. Check `worker_max_threads`, `active_jobs`, `recent_jobs`, and `timeout_abandonment_summary`; do not infer correctness from process names alone. `lgrep_status_semantic(path="")` is cheap/memory-only; pass a specific `path` for deep file/chunk counts. Shared HTTP destructive prune requests are forced to dry-run; run `lgrep prune-orphans --execute` locally for intentional deletion.

**Recommended — one command:**
```bash
lgrep install-opencode
```

**Recommended per-project ignore file:**
```bash
lgrep init-ignore /path/to/project
```
This creates a default `.lgrepignore` template you can customize.

**Prune orphan semantic caches:**
```bash
lgrep prune-orphans --dry-run            # inspect only (default)
lgrep prune-orphans --execute            # delete orphan cache dirs
lgrep prune-orphans --cache-dir /tmp/x   # one-off cache root override

# Tune the grace window (default 1h; protects caches mid-indexing):
LGREP_PRUNE_MIN_AGE_S=0    lgrep prune-orphans --execute   # aggressive, no grace
LGREP_PRUNE_MIN_AGE_S=7200 lgrep prune-orphans --dry-run   # 2h grace window
```
Dry-run by default. Active projects and the `symbols/` subdir are always skipped. `--execute` and `--dry-run` are mutually exclusive.

**MCP safety.** The `lgrep_prune_orphans` MCP tool forces `dry_run=True` on non-stdio (shared HTTP) transports. Destructive prunes on shared servers must use the CLI.

This installs three artifacts into `~/.config/opencode/`: the MCP server entry,
the always-on `instructions/lgrep-tools.md` policy file, and this skill file.
To remove them: `lgrep uninstall-opencode`.

**Manual** — add to `~/.config/opencode/opencode.json`:
```json
{
  "mcp": {
    "lgrep": { "type": "remote", "url": "http://localhost:6285/mcp" }
  }
}
```

## Semantic Engine Tools

> **Note:** Tool functions are named `search_semantic`, `index_semantic`, etc. OpenCode auto-prefixes them as `lgrep_search_semantic`, `lgrep_index_semantic`, etc.

### lgrep_search_semantic

Searches a project semantically.

- `q` (string, **required**): Natural language search query. Alias: `query`.
- `path` (string, **required**): Absolute path to the project to search. Auto-loads from disk if previously indexed in a prior session.
- `m` (int): Maximum results (default 10). Alias: `limit`.
- `hybrid` (bool): Use hybrid search (default true). Combines vector + keyword search.

If a default hybrid semantic search times out or hits a deadline, retry once with `hybrid:false` and a small limit (for example `m=5` / `limit=5`) before falling back to symbol/text/read tools.

**Example usage:**
```python
# Short form (preferred by agents)
lgrep_search_semantic(q="JWT verification and token handling", path="/home/user/dev/project", m=5)

# Long form (also accepted)
lgrep_search_semantic(query="JWT verification and token handling", path="/home/user/dev/project", limit=5)
```

### lgrep_index_semantic

Indexes a project for semantic search. Call this once per project to build the initial index, or to force a full refresh. **Not required after server restart** — `lgrep_search_semantic` auto-loads existing disk indexes. **Not required when files change** — `lgrep_search_semantic` runs a built-in staleness check and re-indexes drifted files automatically (see *Staleness Handling* below).

- `path` (string, **required**): Absolute path to project root.

### lgrep_status_semantic

Check semantic index status and statistics.

- `path` (string, optional): Absolute path to project. If omitted, returns stats for **all** in-memory projects.

## Staleness Handling

`lgrep_search_semantic` is fresh-by-default. Before every search it runs a
three-stage check:

1. **mtime gate** — walks current files, compares each `stat().st_mtime` to the
   index's latest `indexed_at` timestamp. Also checks the indexed file-set
   size against current. Warm path (no edits since last index) typically
   completes in single-digit milliseconds.
2. **hash check** — only files whose mtime is newer than the index are
   SHA-256-hashed and compared against the stored hash from a single
   batched LanceDB projection query.
3. **re-index** — on confirmed drift, `index_all()` runs via the existing
   single-flight coordinator so concurrent searches share one re-index.

Agents do **not** need to manually call `lgrep_index_semantic` to refresh
between searches. Call `lgrep_status_semantic` if a project's drift behavior
seems wrong (e.g., to inspect `disk_cache` / `watching` state per project).

### lgrep_watch_start_semantic

Start watching a directory for file changes (auto-reindex on save).

- `path` (string, **required**): Absolute path to project root.

### lgrep_watch_stop_semantic

Stop watching for file changes.

- `path` (string, optional): Absolute path to project to stop watching. If omitted, stops **all** watchers.

## Symbol Engine Tools

> Symbol tools use `index_symbols_folder`, `search_symbols`, etc. OpenCode prefixes them as `lgrep_index_symbols_folder`, `lgrep_search_symbols`, etc.

### Symbol IDs

Symbol IDs use the deterministic format `file_path:kind:name`:
```
src/auth.py:function:authenticate
src/auth.py:class:AuthManager
src/auth.py:method:login
```

### lgrep_index_symbols_folder

Index all symbols in a local folder. Run once before using `lgrep_search_symbols` or `lgrep_get_symbol`.

- `path` (string, **required**): Absolute path to the repository/folder root.
- `max_files` (int): Maximum files to index (default: 500).
- `incremental` (bool): Skip files whose SHA-256 hash matches the stored index (default: `true`). Set to `false` to force a full re-index.

### lgrep_index_symbols_repo

Index symbols from a GitHub repository via the REST API (no git clone).

- `repo` (string, **required**): GitHub repo in `owner/name` format.
- `ref` (string): Branch, tag, or commit SHA (default: `HEAD`).

### lgrep_list_repos

List all repositories that have been indexed in the symbol store.

### lgrep_get_file_tree

Get the file tree of a repository (respects .gitignore). **No index needed.**

- `path` (string, **required**): Absolute path to the repository root.

### lgrep_get_file_outline

Get the symbol outline (functions, classes, methods) for a single file. **No index needed.**

- `path` (string, **required**): Absolute path to the source file.

**Example usage:**
```python
lgrep_get_file_outline(path="/home/user/dev/project/src/auth.py")
```

### lgrep_get_repo_outline

Get the symbol outline across an entire repository.

- `path` (string, **required**): Absolute path to the repository root.

### lgrep_search_symbols

Search for symbols by name (case-insensitive substring match). Requires prior indexing with `lgrep_index_symbols_folder`.

- `query` (string, **required**): Symbol name to search for.
- `path` (string, **required**): Absolute path to the indexed repository.
- `limit` (int): Maximum results (default: 20).
- `kind` (string, optional): Filter by kind (`function`, `class`, `method`, `interface`).

**Example usage:**
```python
lgrep_search_symbols(query="authenticate", path="/home/user/dev/project")
```

### lgrep_search_text

Literal text search across all source files.

- `query` (string, **required**): Text to search for.
- `path` (string, **required**): Absolute path to the repository root.
- `max_results` (int): Maximum results (default: 50).
- `case_sensitive` (bool): Case-sensitive matching (default: false).

### lgrep_get_symbol

Get full metadata and source code for a single symbol by ID.

- `symbol_id` (string, **required**): Symbol ID in format `file_path:kind:name`.
- `path` (string, **required**): Absolute path to the indexed repository.

### lgrep_get_symbols

Batch retrieval of multiple symbols by ID.

- `symbol_ids` (list[string], **required**): List of symbol IDs.
- `path` (string, **required**): Absolute path to the indexed repository.

### lgrep_invalidate_cache

Remove the symbol index for a repository, forcing a full re-index on next use.

- `path` (string, **required**): Absolute path to the repository root.

## Best Practices

1. **Ignore large or generated files (`.lgrepignore`)**: `lgrep` respects `.gitignore` automatically. For additional exclusions, create a `.lgrepignore` file in the project root (e.g. `src/generated/`, `*.test.data`) to speed up indexing and avoid clutter.
2. **Semantic search — be specific**: Instead of "auth", use "JWT authentication flow and session management".
3. **Symbol search — use after indexing**: Run `lgrep_index_symbols_folder` once per project before using `lgrep_search_symbols` or `lgrep_get_symbol`.
4. **File outline — no index needed**: `lgrep_get_file_outline` works immediately without any prior indexing.
5. **Hybrid is better**: Keep `hybrid=true` (default) for semantic search — it combines keyword precision with semantic breadth.
6. **Just search semantically**: After initial indexing, `lgrep_search_semantic` auto-loads from disk on server restart. No need to re-run `lgrep_index_semantic` each session.
7. **Auto-fresh by default**: `lgrep_search_semantic` re-indexes drifted files automatically. Only run `lgrep_index_semantic` explicitly to force a full rebuild.
8. **Always pass `path`**: Both engines require an explicit project path — they do not auto-detect the current project.
9. **Use `LGREP_WARM_PATHS`**: Set this env var to a colon-separated list of project paths in your MCP config to pre-load semantic indexes at server startup.
10. **For shared daemons, bound runtime work**: Pair explicit warm paths with `LGREP_AUTO_WARM_DISK=false`, `LGREP_WORKTREE_DEDUP=1`, `LGREP_TOOL_TIMEOUT_S`, and `LGREP_WORKER_MAX_THREADS`.
11. **MCP registration is transport, not policy**: Keep lgrep registered as MCP and enforce tool-choice behavior via this decision matrix.

## Supported Languages (Symbol Engine)

Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, C#, PHP, Ruby, Swift, Kotlin — 14 languages with full function/class/method extraction. The semantic engine supports 30+ languages via AST-aware chunking.

## Keywords
semantic search, code search, grep, find code, search files, local search,
code exploration, find implementation, natural language search, concept search,
search codebase, understand code, find related code, symbol search, function lookup,
class lookup, file outline, repo outline, AST, tree-sitter, refactoring, rename symbol
