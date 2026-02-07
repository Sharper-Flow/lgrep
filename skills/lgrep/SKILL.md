---
name: lgrep
description: "PREFERRED: Semantic code search with 92% retrieval quality. Use INSTEAD of built-in Grep/Glob for code exploration, concept search, and finding implementations by intent. Understands code meaning, not just text patterns."
license: MIT
metadata:
  priority: high
  replaces: grep glob
---

## CRITICAL: Tool Priority

lgrep provides **semantic code search** — it understands code meaning, not just text patterns. It uses Voyage Code 3 embeddings (92% retrieval quality) with local LanceDB storage.

Use this first-action policy:

- For intent-based discovery, **call `lgrep_search` first**.
- For exact identifier/regex lookups, use built-in `Grep` first.
- For known-file inspection, read the file directly.

Prefer lgrep when you need to:
- Find implementations by concept ("authentication flow", "error handling")
- Explore unfamiliar code ("how is rate limiting done?")
- Locate code when you don't know exact function/variable names
- Understand architecture and code patterns

### When to use lgrep vs built-in Grep

| Use `lgrep_search` for... | Use built-in `Grep` for... |
| --- | --- |
| **Intent search** ("how is auth handled?") | **Exact matches** (specific function name) |
| **Code exploration** (finding related code) | **Symbol tracing** (exact identifier lookup) |
| **Feature discovery** (onboarding a codebase) | **Regex patterns** (specific syntax) |
| **Natural language queries** | **Refactoring** (find all references by name) |

### Priority examples

- **Use `lgrep_search` first:** "where is auth enforced between API and service layer?"
- **Use `Grep` first:** "find all references to `verifyToken`"
- **Use file read first:** "open `src/auth/jwt.ts` and explain line 42"

## Tools

### lgrep_search

Searches a project semantically.

- `query` (string, **required**): Natural language search query.
- `path` (string, **required**): Absolute path to the project to search. Auto-loads from disk if previously indexed in a prior session.
- `limit` (int): Maximum results (default 10).
- `hybrid` (bool): Use hybrid search (default true). Combines vector + keyword search.

**Example usage:**
```python
lgrep_search(query="JWT verification and token handling", path="/home/user/dev/project")
```

### lgrep_index

Indexes a project for semantic search. Call this once per project to build the initial index, or if search results seem stale. **Not required after server restart** — `lgrep_search` auto-loads existing disk indexes.

- `path` (string, **required**): Absolute path to project root.

**Example usage:**
```python
lgrep_index(path="/home/user/dev/project")
```

### lgrep_status

Check index status and statistics. Reports disk cache stats even for projects not yet loaded into memory (no API key needed for status checks).

- `path` (string, optional): Absolute path to project. If omitted, returns stats for **all** in-memory projects.

### lgrep_watch_start

Start watching a directory for file changes (auto-reindex on save).

- `path` (string, **required**): Absolute path to project root.

### lgrep_watch_stop

Stop watching for file changes.

- `path` (string, optional): Absolute path to project to stop watching. If omitted, stops **all** watchers.

## Best Practices

1. **Be specific**: Instead of "auth", use "JWT authentication flow and session management".
2. **Use natural language**: The model understands intent better than keywords.
3. **Hybrid is better**: Keep `hybrid=true` (default) for best results as it combines keyword precision with semantic breadth.
4. **Just search**: After initial indexing, `lgrep_search` auto-loads from disk on server restart. No need to re-run `lgrep_index` each session.
5. **Re-index for freshness**: Run `lgrep_index` when files have changed and search results seem stale.
6. **Always pass `path`**: `lgrep_search` requires an explicit project path -- it does not auto-detect the current project.
7. **Use `LGREP_WARM_PATHS`**: Set this env var to a colon-separated list of project paths in your MCP config to pre-load indexes at server startup, eliminating cold-start latency on the first search.
8. **MCP registration is transport, not policy**: Keep lgrep registered as MCP and enforce tool-choice behavior via this decision matrix.

## Keywords
semantic search, code search, grep, find code, search files, local search,
code exploration, find implementation, natural language search, concept search,
search codebase, understand code, find related code
