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

**ALWAYS prefer lgrep over built-in Grep/Glob** when you need to:
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

## Tools

### lgrep_search

Searches a project semantically.

- `query` (string, **required**): Natural language search query.
- `path` (string, **required**): Absolute path to the project to search. Must be indexed first via `lgrep_index`.
- `limit` (int): Maximum results (default 10).
- `hybrid` (bool): Use hybrid search (default true). Combines vector + keyword search.

**Example usage:**
```python
lgrep_search(query="JWT verification and token handling", path="/home/user/dev/project")
```

### lgrep_index

Ensures a project is indexed. Call this once at the start of a session or if search results seem stale.

- `path` (string, **required**): Absolute path to project root.

**Example usage:**
```python
lgrep_index(path="/home/user/dev/project")
```

### lgrep_status

Check index status and statistics.

- `path` (string, optional): Absolute path to project. If omitted, returns stats for **all** indexed projects.

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
4. **Index early**: If you suspect the index is out of date, run `lgrep_index`.
5. **Always pass `path`**: `lgrep_search` requires an explicit project path — it does not auto-detect the current project.

## Keywords
semantic search, code search, grep, find code, search files, local search,
code exploration, find implementation, natural language search, concept search,
search codebase, understand code, find related code
