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

1. **Semantic engine** (`lgrep_search_semantic`) — understands code *meaning*. Uses Voyage Code 4 embeddings with local LanceDB storage.
2. **Symbol engine** (`lgrep_search_symbols`, `lgrep_get_file_outline`, etc.) — understands code *structure*. Exact function/class/method lookup via tree-sitter AST. No API key needed.

Use this first-action policy:

- For intent-based discovery, **call `lgrep_search_semantic` first**.
- For exact symbol lookup by name, **call `lgrep_search_symbols`** (after indexing).
- For candidate usage/reference lookup for a symbol name, **call `lgrep_search_references`** (after indexing).
- For file structure overview, **call `lgrep_get_file_outline`** (no index needed).
- For exact identifier/regex lookups, use built-in `Grep` first.
- For known-file inspection, read the file directly.

### Decision Matrix

| Use case | Best tool | Notes |
|---|---|---|
| **Intent search** ("how is auth handled?") | `lgrep_search_semantic` | Semantic retrieval finds meaning |
| **Find function by name** ("find authenticate") | `lgrep_search_symbols` | Exact symbol lookup, fast |
| **Find candidate usages** ("where is helper used?") | `lgrep_search_references` | Candidate occurrences, not exhaustive |
| **File structure** ("what's in auth.py?") | `lgrep_get_file_outline` | No index needed |
| **Repo structure** ("what's in this codebase?") | `lgrep_get_repo_outline` | Full symbol map |
| **Exact text/identifier** ("find verifyToken") | `lgrep_search_text` or `Grep` | Literal matching |
| **Get symbol source** (by ID) | `lgrep_get_symbols` | Byte-precise retrieval; one or more IDs |
| **Known-file review** | `Read` | Direct inspection |

### Priority examples

- **Use `lgrep_search_semantic` first:** "where is auth enforced between API and service layer?"
- **Use `lgrep_search_symbols` first:** "find the `authenticate` function"
- **Use `lgrep_search_references` first:** "find candidate usages of `authenticate`"
- **Use `lgrep_get_file_outline` first:** "what functions are in `src/auth.py`?"
- **Use `Grep` first:** "find all references to `verifyToken`"
- **Use file read first:** "open `src/auth/jwt.ts` and explain line 42"

### Tool Exposure Requirement

Instruction text alone is not enough. The active agent or sub-agent also needs
the `lgrep_*` tool definitions in its tool manifest.

- If the manifest omits `lgrep_search_semantic`, `lgrep_search_symbols`,
  `lgrep_search_references`, or related `lgrep_*` tools, the model cannot
  follow this routing policy and will fall back to `glob`/`grep`/`read`.
- In agent frontmatter, explicitly allow the tools you expect the agent to use (for
  example `lgrep_search_semantic: true`, `lgrep_search_symbols: true`,
  `lgrep_search_references: true`, `lgrep_get_file_outline: true`,
  `lgrep_search_text: true`).

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

**MCP safety.** Over MCP, `lgrep_prune_orphans` and `lgrep_prune_symbols` delete only when the server sets `LGREP_ALLOW_DESTRUCTIVE_MCP`; otherwise they return a preview and say why. Destructive prunes on shared servers should use the CLI.

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

> **Argument names:** every tool declares `query` (search text or symbol name), `path` (local repository root or file), `limit` (result cap), and `max_files` (file input/parse budget; incremental symbol indexing counts changed/new parses, not unchanged files). Before validation the server renames fixed legacy spellings — `max_results`/`maxResults` → `limit`, `symbol`/`symbol_name` → `query`, `pattern` → `query` on `lgrep_search_text` only, `file_path`/`file`/`folder` → `path` — and refuses any other undeclared argument, naming the tool's valid arguments. `symbol_id` on `lgrep_search_references` and `path` on `lgrep_index_symbols_repo` are refused with the tool and argument to use instead (`lgrep_get_symbols` with `symbol_ids` / `lgrep_index_symbols_folder` with `path`).

### lgrep_search_semantic

Searches a project semantically.

- `query` (string, **required**): Natural language search query.
- `path` (string, **required**): Absolute path to the project to search. Auto-loads from disk if previously indexed in a prior session.
- `limit` (int): Maximum results (default 10).
- `hybrid` (bool): Use hybrid search (default true). Combines vector + keyword search.
- `include_content` (bool): Add the full stored chunk text as `content` on each hit (default false).

Default hits are compact: `file_path`, `start_line`, `end_line`, `score`, `match_type`, and `snippet` (first 3 non-blank chunk-body lines, 120 characters each). Read only the hits you need — pass `include_content=true` when you want the full chunk text inline. `line_number` is no longer returned; `start_line` carries that value.

If a default hybrid semantic search times out or hits a deadline, retry once with `hybrid:false` and a small limit (for example `limit=5`) before falling back to symbol/text/read tools.

**Example usage:**
```python
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

The whole check is bounded by `LGREP_STALENESS_DEADLINE_S` (default 4.0s)
so a large repo's directory walk cannot eat the entire 8s tool timeout.
On deadline, the search proceeds with the slightly-stale index and a
`staleness_check_deadline_exceeded` log is emitted; the next search will
trigger a fresh reindex if drift is real. Agents may see hybrid
false-positives (results that don't reflect very recent edits) right
after a long staleness walk; re-run the search once if precision is
critical.

Re-index work (`index_all`) is cooperatively cancellable: when the awaiting
MCP coroutine is cancelled (8s tool timeout), the bounded-executor worker
thread unwinds at the next blocking seam — between files, between embed
batches, and even during the Voyage retry backoff (`cancel_event.wait`
replaces an un-cancellable sleep) — so a single slow file or long retry
cannot wedge the worker pool. A hard wall-clock backstop,
`LGREP_INDEX_MAX_WALL_S` (default 60.0s), guarantees `index_all` aborts the
batch with `index_all_wall_clock_exceeded` regardless of where it blocks.
Abandoned jobs reach a terminal `FINISHED_AFTER_ABANDON`/`CANCELLED` state
and `lgrep_diagnostics` `active_job_count` returns to 0.

Agents do **not** need to manually call `lgrep_index_semantic` to refresh
between searches. Call `lgrep_status_semantic` if a project's drift behavior
seems wrong (e.g., to inspect `disk_cache` / `watching` state per project).

Background file watchers are server configuration, not a tool: set
`LGREP_AUTO_WATCH=true` to watch projects warmed at startup.

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

Submit a local background job on stdio. The acknowledgement contains `job_id`,
`state`, and `duplicate`, not final counters. Read `lgrep_get_symbol_index_job`
before using `lgrep_search_symbols` or `lgrep_get_symbols`.

- `path` (string, **required**): Absolute path to the repository/folder root.
- `max_files` (int): Changed/new file parse cap (default: 500); unchanged files do not consume it. A separate default 150-second cooperative budget, configured by `LGREP_EXPLICIT_INDEX_BUDGET_S`, also bounds the job. Deadline stops report `truncation_reason="deadline"` in status.
- `incremental` (bool): Skip files whose SHA-256 hash matches the stored index (default: `true`). Set to `false` to force a full re-index.

### lgrep_index_symbols_repo

Submit a background GitHub API job on stdio (no git clone). Read final counters
from `lgrep_get_symbol_index_job`, not the submission acknowledgement.

- `repo` (string, **required**): GitHub repo in `owner/name` format.
- `ref` (string): Branch, tag, or commit SHA (default: `HEAD`).
- `max_files` (int): File fetch/parse cap (default: 500). Jobs also use the default 150-second cooperative budget, configured by `LGREP_EXPLICIT_INDEX_BUDGET_S`; deadline stops report `truncation_reason="deadline"` in status.
- `github_token` (string, optional): Explicit credential; otherwise `LGREP_GITHUB_TOKEN`, then `GITHUB_TOKEN`.

### lgrep_get_symbol_index_job

- `job_id` (string, **required**): ID returned by a submission in this daemon.
- States: `queued`, `running`, `completed`, `failed`, `cancelled` (shutdown only).
- A completed job returns final counters in `result`. Check `walk_truncated`
  (folder) or `truncated` (remote), and `truncation_reason`: `"max_files"`,
  `"deadline"`, or null. Completion with truncation is not full repository coverage.
- Identical nonterminal submissions join one job. Different ones return
  `symbol_index_busy`, retryable=true. Retry later, rather than submitting in parallel.
- Only the latest 100 terminal explicit jobs are retained; unrelated queries do
  not evict them. Restart loses jobs. Unknown/evicted IDs return
  `unknown_symbol_index_job`, retryable=false. There is no public cancel tool.
- These three tools are listed only when the server runs on stdio. A server
  started with `--transport streamable-http` does not offer them; symbol tools
  there build a missing index on first query instead.

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
- `limit` (int): Maximum results (default: 50).
- `case_sensitive` (bool): Case-sensitive matching (default: false).

### lgrep_search_references

Search for candidate references/usages of a symbol name in an indexed Python repository.

- `query` (string, **required**): Symbol name to look up (e.g. `helper`).
- `path` (string, **required**): Absolute path to the indexed repository.
- `limit` (int): Maximum results (default: 20).
- `usage_filter` (string): One of `production_first` (default), `include_tests`, or `tests_only`.
- `kind` (string, optional): Occurrence kind filter (`call`, `attribute`, `import`, `reference`).

Results are **candidate occurrences only** — they are not compiler-accurate or exhaustive. Each result includes the file path, line number, line text, occurrence kind, and the nearest enclosing symbol. Use `lgrep_get_file_outline` or `lgrep_get_symbols` to inspect the surrounding code in more detail.

**Example usage:**
```python
lgrep_search_references(query="helper", path="/home/user/dev/project", usage_filter="production_first")
```

### lgrep_get_symbols

Get full metadata and source code for one or more symbols by ID. Pass every
relevant ID in one call.

- `symbol_ids` (list[string], **required**): One or more IDs in format `file_path:kind:name`.
- `path` (string, **required**): Absolute path to the indexed repository.

The response holds one entry per requested ID, in order. An unknown ID or a
source range that no longer matches the file returns `{"id": ..., "error": ...}`
in that entry rather than a top-level error, so check each entry.

### lgrep_invalidate_cache

Remove the symbol index for a repository, forcing a full re-index on next use.
Listed only when the server sets `LGREP_ALLOW_DESTRUCTIVE_MCP`.

- `path` (string, **required**): Absolute path to the repository root.

## Best Practices

1. **Ignore large or generated files (`.lgrepignore`)**: `lgrep` respects `.gitignore` automatically. For additional exclusions, create a `.lgrepignore` file in the project root (e.g. `src/generated/`, `*.test.data`) to speed up indexing and avoid clutter.
2. **Semantic search — be specific**: Instead of "auth", use "JWT authentication flow and session management".
3. **Symbol search — no setup for a checkout root**: `lgrep_search_symbols`, `lgrep_search_references`, and `lgrep_get_symbols` index a missing local git checkout on first use. A subdirectory query needs `lgrep_index_symbols_folder` first (stdio servers only).
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
