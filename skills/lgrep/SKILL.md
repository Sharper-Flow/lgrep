---
name: lgrep
description: "PREFERRED: Dual-engine code intelligence (semantic search + symbol lookup). Use INSTEAD of built-in Grep/Glob for code exploration, concept search, and finding implementations by intent. Semantic engine: 92% retrieval quality. Symbol engine: exact function/class lookup, no API key needed."
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

## Setup

**Recommended — one command:**
```bash
lgrep install-opencode
```

This installs a custom tool wrapper, MCP server entry, and this skill file into `~/.config/opencode/`. To remove: `lgrep uninstall-opencode`.

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

**Example usage:**
```python
# Short form (preferred by agents)
lgrep_search_semantic(q="JWT verification and token handling", path="/home/user/dev/project", m=5)

# Long form (also accepted)
lgrep_search_semantic(query="JWT verification and token handling", path="/home/user/dev/project", limit=5)
```

### lgrep_index_semantic

Indexes a project for semantic search. Call this once per project to build the initial index, or if search results seem stale. **Not required after server restart** — `lgrep_search_semantic` auto-loads existing disk indexes.

- `path` (string, **required**): Absolute path to project root.

### lgrep_status_semantic

Check semantic index status and statistics.

- `path` (string, optional): Absolute path to project. If omitted, returns stats for **all** in-memory projects.

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

1. **Semantic search — be specific**: Instead of "auth", use "JWT authentication flow and session management".
2. **Symbol search — use after indexing**: Run `lgrep_index_symbols_folder` once per project before using `lgrep_search_symbols` or `lgrep_get_symbol`.
3. **File outline — no index needed**: `lgrep_get_file_outline` works immediately without any prior indexing.
4. **Hybrid is better**: Keep `hybrid=true` (default) for semantic search — it combines keyword precision with semantic breadth.
5. **Just search semantically**: After initial indexing, `lgrep_search_semantic` auto-loads from disk on server restart. No need to re-run `lgrep_index_semantic` each session.
6. **Re-index for freshness**: Run `lgrep_index_semantic` when files have changed and search results seem stale.
7. **Always pass `path`**: Both engines require an explicit project path — they do not auto-detect the current project.
8. **Use `LGREP_WARM_PATHS`**: Set this env var to a colon-separated list of project paths in your MCP config to pre-load semantic indexes at server startup.
9. **MCP registration is transport, not policy**: Keep lgrep registered as MCP and enforce tool-choice behavior via this decision matrix.

## Keywords
semantic search, code search, grep, find code, search files, local search,
code exploration, find implementation, natural language search, concept search,
search codebase, understand code, find related code, symbol search, function lookup,
class lookup, file outline, repo outline, AST, tree-sitter, refactoring, rename symbol
