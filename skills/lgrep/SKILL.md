# Skill: lgrep (Semantic Code Search)

`lgrep` provides high-quality semantic search across codebases using Voyage Code 3 embeddings and local LanceDB storage. It supports **multiple concurrent projects** with isolated indexes — each project gets its own database and watcher.

## When to use

- **Concept search**: "How is authentication handled?", "Where is the database connection pooled?"
- **Fuzzy search**: "Find code related to rate limiting", "Find examples of error handling in API routes"
- **Natural language queries**: When you don't know the exact function names or variable names.
- **Replacing grep/mgrep**: When keyword search returns too many results or misses conceptually related code.

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

**Example usage:**
```python
# All projects
lgrep_status()

# Single project
lgrep_status(path="/home/user/dev/project")
```

### lgrep_watch_start

Start watching a directory for file changes (auto-reindex on save).

- `path` (string, **required**): Absolute path to project root.

**Example usage:**
```python
lgrep_watch_start(path="/home/user/dev/project")
```

### lgrep_watch_stop

Stop watching for file changes.

- `path` (string, optional): Absolute path to project to stop watching. If omitted, stops **all** watchers.

**Example usage:**
```python
# Stop one project
lgrep_watch_stop(path="/home/user/dev/project")

# Stop all watchers
lgrep_watch_stop()
```

## Multi-Project Support

lgrep can index and search multiple projects concurrently. Each project gets its own isolated LanceDB database and file watcher. A single embedding model (Voyage Code 3) is shared across all projects.

- **Maximum projects**: 20 concurrent projects (each holds a LanceDB connection + optional watcher thread).
- **Isolation**: Search results from project A never include files from project B.
- **Eviction**: Use `lgrep remove <path>` CLI command to inspect project state. Restart the server to evict projects from memory. On-disk indexes are preserved.

## Best Practices

1. **Be specific**: Instead of "auth", use "JWT authentication flow and session management".
2. **Use natural language**: The model understands intent better than keywords.
3. **Hybrid is better**: Keep `hybrid=true` (default) for best results as it combines keyword precision with semantic breadth.
4. **Index early**: If you suspect the index is out of date, run `lgrep_index`.
5. **Always pass `path`**: `lgrep_search` requires an explicit project path — it does not auto-detect the current project.
