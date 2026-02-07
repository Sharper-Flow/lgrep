# Archive: Multi-project support: concurrent indexing, searching, and watching across multiple projects without interference

**Change ID:** multiProjectSupportConcurrentI
**Archived:** 2026-02-07T00:08:53.855Z
**Created:** 2026-02-06T23:28:00.379Z

## Tasks Completed

- ✅ Introduce `ProjectState` dataclass and refactor `LgrepContext` to hold `dict[str, ProjectState]` instead of single-project fields. Share one `VoyageEmbedder` across all projects.
- ✅ Refactor `_ensure_project_initialized` to look up or create a `ProjectState` in the dict, never replacing existing entries.
- ✅ Add required `path` parameter to `lgrep_search`. Look up the project's `ChunkStore` from the cached dict. Return clear error if path not indexed.
- ✅ Add optional `path` parameter to `lgrep_status`. Without path: return stats for all indexed projects. With path: return stats for that project only.
- ✅ Refactor `lgrep_index` to use the new multi-project `_ensure_project_initialized` (it already takes `path`, so mainly update context access pattern).
- ✅ Refactor `lgrep_watch_start` and `lgrep_watch_stop` for multi-project: support concurrent watchers per project. `watch_stop` takes optional `path` to stop a specific watcher.
- ✅ Update `app_lifespan` shutdown to iterate all `ProjectState` entries and stop their watchers.
- ✅ Update `skills/lgrep/SKILL.md` to document the `path` parameter on `lgrep_search`, `lgrep_status`, and `lgrep_watch_stop`.
- ⏭️ Update Vision MCP server registry entry for lgrep to reflect new tool signatures (search requires path).
- ✅ Add `asyncio.Lock` to `LgrepContext` to guard `_ensure_project_initialized` against concurrent access. Two simultaneous tool calls for the same project path must not create duplicate `ProjectState` entries or corrupt the dict. The lock should be per-context (not per-project) since dict mutation is the critical section.
- ✅ Add multi-project integration test: index project A and project B (separate tmp dirs), then search A and verify results come only from A's files, search B and verify results come only from B's files. Also verify lgrep_status without path returns both projects, and with path returns only the specified one.
- ✅ Update existing tests in `test_server.py` and `test_integration.py` to match new multi-project API signatures: `lgrep_search` now takes `path`, `lgrep_status` takes optional `path`, `lgrep_watch_stop` takes optional `path`. Update `LgrepContext` construction to use new `projects` dict structure.
- ✅ Add `project=` key to all structured log entries in multi-project tool functions (`lgrep_search`, `lgrep_index`, `lgrep_status`, `lgrep_watch_start`, `lgrep_watch_stop`) so logs can be filtered by project path when debugging concurrent operations.
- ✅ Add a MAX_PROJECTS constant (e.g., 20) and check in `_ensure_project_initialized` before creating new `ProjectState` entries. Log a warning when approaching the limit (80%) and return an error at the limit. Each project holds a LanceDB connection + potential watcher thread, so unbounded growth risks resource exhaustion. Document the limit in SKILL.md.

## Specs Modified

