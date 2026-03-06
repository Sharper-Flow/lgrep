# lgrep Tool Selection Policy

This instruction is designed to be always loaded by OpenCode so agents reliably
prefer `lgrep` for local code exploration.

## Local Code Exploration (Critical)

**lgrep is the PRIMARY tool for local code exploration.** It provides semantic
search (understands code meaning) and symbol search (exact function/class lookup
via AST). When lgrep is available, it MUST be the first tool you reach for when
exploring code.

### First-Action Policy (Mandatory)

Before using `glob` or `grep` for code exploration, check if lgrep can answer
the question:

| Query type | First tool | Example |
|---|---|---|
| Intent/concept discovery | `lgrep_search_semantic` | "how is auth handled?", "where is rate limiting?" |
| Find function/class by name | `lgrep_search_symbols` | "find the authenticate function" |
| File structure overview | `lgrep_get_file_outline` | "what's in auth.py?" |
| Repo structure overview | `lgrep_get_repo_outline` | "what's in this codebase?" |
| Exact text/regex lookup | `lgrep_search_text` or `grep` | "find all references to verifyToken" |
| Known file inspection | `read` | "open src/auth.ts and explain line 42" |

### When to Use Built-in grep/glob Instead

- The query is explicitly an exact-match text/regex search
- lgrep failed or timed out once this turn; fall back immediately, do not retry
- lgrep MCP server is not running or not configured

### Anti-Patterns (Do NOT Do These)

- Do NOT start with `glob` then `grep` then `read` for concept queries; use `lgrep_search_semantic`
- Do NOT use `grep` to find a function by name; use `lgrep_search_symbols`
- Do NOT use `glob` to discover repo structure; use `lgrep_get_repo_outline` or `lgrep_get_file_tree`
- Do NOT skip lgrep because "grep is simpler"

### Agent Override

These rules apply even if an agent-specific prompt suggests a more generic
search order. For local code exploration, this instruction overrides defaults
like "glob first" or "grep first".
