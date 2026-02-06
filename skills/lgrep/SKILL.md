# Skill: lgrep (Semantic Code Search)

`lgrep` provides high-quality semantic search across the current codebase using Voyage Code 3 embeddings and local LanceDB storage. Use it when you need to find code by meaning rather than just keywords.

## When to use

- **Concept search**: "How is authentication handled?", "Where is the database connection pooled?"
- **Fuzzy search**: "Find code related to rate limiting", "Find examples of error handling in API routes"
- **Natural language queries**: When you don't know the exact function names or variable names.
- **Replacing grep/mgrep**: When keyword search returns too many results or misses conceptually related code.

## Tools

### lgrep_search

Searches the codebase semantically.

- `query` (string): Natural language search query.
- `limit` (int): Maximum results (default 10).
- `hybrid` (bool): Use hybrid search (default true). Combines vector + keyword search.

**Example usage:**
```python
lgrep_search(query="JWT verification and token handling")
```

### lgrep_index

Ensures a project is indexed. Call this once at the start of a session or if search results seem stale.

- `path` (string): Absolute path to project root.

**Example usage:**
```python
lgrep_index(path="/home/user/dev/project")
```

### lgrep_status

Check if the current project is indexed and how many chunks are stored.

**Example usage:**
```python
lgrep_status()
```

## Best Practices

1. **Be specific**: Instead of "auth", use "JWT authentication flow and session management".
2. **Use natural language**: The model understands intent better than keywords.
3. **Hybrid is better**: Keep `hybrid=true` (default) for best results as it combines keyword precision with semantic breadth.
4. **Index early**: If you suspect the index is out of date, run `lgrep_index`.
