# Change: Multi-project support for concurrent indexing, searching, and watching

## Why

lgrep currently holds a single-project context in `LgrepContext`. When an agent calls
`lgrep_index(path=...)` for a different project, it **replaces** the active context entirely.
This means:

- Agents in different repos sharing one lgrep MCP server interfere with each other
- `lgrep_search` has no `path` parameter -- it always searches the last-indexed project
- Switching projects silently breaks search for any agent that was using the previous project
- Only one watcher can run at a time

This is a critical gap. Our use case requires agents in `pokeedge-web` and `pokeedge`
(and potentially more repos) to independently index and search their own codebases
through a single shared lgrep MCP server, without interference.

## What Changes

1. **`LgrepContext`**: Replace single-project fields (`db`, `indexer`, `watcher`,
   `project_path`, `watching`) with a `dict[str, ProjectState]` keyed by resolved
   project path. Each `ProjectState` holds that project's `ChunkStore`, `Indexer`,
   `FileWatcher`, and watching status.

2. **`_ensure_project_initialized`**: Look up the project in the dict; create and cache
   a new `ProjectState` if it doesn't exist. Never replace an existing project's state.

3. **`lgrep_search`**: Add required `path` parameter so agents specify which project to
   search. The store is looked up from the cached dict -- no re-initialization needed
   for already-indexed projects (ultra-fast).

4. **`lgrep_status`**: Add optional `path` parameter. With path: return stats for that
   project. Without path: return stats for all indexed projects.

5. **`lgrep_watch_start` / `lgrep_watch_stop`**: Support multiple concurrent watchers,
   each tied to its own project. `watch_stop` takes a `path` param to stop a specific
   watcher, or stops all if omitted.

6. **`app_lifespan` shutdown**: Iterate all `ProjectState` entries and stop their watchers.

7. **Skill doc**: Update `SKILL.md` to document the `path` parameter on `lgrep_search`.

8. **Vision registry**: Update the Vision MCP server entry for lgrep if needed to reflect
   new tool signatures.

## Success Criteria

Each criterion is specific, testable, and independent:

1. [ ] Calling `lgrep_index(path="/project-A")` then `lgrep_index(path="/project-B")` results in
      both projects being independently accessible -- neither replaces the other
2. [ ] `lgrep_search(query="...", path="/project-A")` returns results only from project A,
      even if project B was indexed more recently
3. [ ] `lgrep_search(query="...", path="/project-B")` returns results only from project B,
      even if project A was indexed more recently
4. [ ] Two concurrent `lgrep_search` calls for different projects both return correct results
      (no cross-contamination)
5. [ ] `lgrep_search` without a `path` parameter returns a clear error message instructing the
      caller to provide a path
6. [ ] `lgrep_status()` (no path) returns stats for all indexed projects
7. [ ] `lgrep_status(path="/project-A")` returns stats for only project A
8. [ ] `lgrep_watch_start(path="/project-A")` and `lgrep_watch_start(path="/project-B")` run
      concurrently -- file changes in A are re-indexed in A's store, changes in B in B's store
9. [ ] `lgrep_watch_stop(path="/project-A")` stops only A's watcher; B continues watching
10. [ ] Server shutdown cleanly stops all active watchers
11. [ ] The shared `VoyageEmbedder` instance is reused across all projects (single API key, no duplication)
12. [ ] SKILL.md documents the `path` parameter on `lgrep_search`
13. [ ] Vision MCP server entry for lgrep is updated if tool signatures changed

## Affected Code

| File | Change |
|------|--------|
| `src/lgrep/server.py` | Core refactor: `LgrepContext`, `_ensure_project_initialized`, all 5 tools |
| `skills/lgrep/SKILL.md` | Document `path` param on search, status, watch tools |
| Vision registry (external) | Update tool signatures if needed |

## Constraints

- MUST: `lgrep_search` requires `path` parameter (breaking change, but necessary for correctness)
- MUST: Existing on-disk LanceDB indexes remain compatible (no migration needed)
- MUST: Single `VoyageEmbedder` instance shared across all projects
- MUST NOT: Load all project indexes eagerly at startup (lazy initialization on first access)
- SHOULD: Keep `ProjectState` cached in memory for fast repeated searches
- SHOULD: Reuse the existing `get_project_db_path()` hashing scheme unchanged

## Impact

- **Affected specs**: None (no specs exist yet)
- **Breaking changes**: Yes -- `lgrep_search` gains a required `path` parameter. Agents using the
  old signature will need to add `path`. This is acceptable because the old behavior was broken
  (silently searching the wrong project).
- **Dependencies**: No new dependencies needed
