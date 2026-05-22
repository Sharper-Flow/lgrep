# Acceptance

Reviewed at: 

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | `lgrep_search_semantic` on a populated cache with >10k chunks does not create/replace FTS/vector indexes on the live query hot path. | pass | tests/test_storage.py::TestSearch::test_search_hybrid_does_not_create_indexes_on_query_path passed; reviewer confirmed `search_hybrid` no longer creates indexes on live query path. |
| AC2 | acceptance_criterion | If hybrid search cannot use ready indexes, lgrep returns useful vector-only results or a structured, actionable error within the configured lgrep timeout. | pass | Vector fallback path exercised by storage test and worktree CLI probe against `/home/jon/dev/advance` returned vector results without timeout. |
| AC3 | acceptance_criterion | The configured lgrep server timeout is lower than the proxy/tool deadline so agents see lgrep-owned errors instead of `context deadline exceeded` where possible. | pass | `~/.config/vision/servers.yaml` was updated, validated, reloaded, and lgrep restarted with `LGREP_TOOL_TIMEOUT_S=8`; hybrid probe returned lgrep-owned structured timeout instead of Vision context deadline. |
| AC4 | acceptance_criterion | Local setup docs or checked-in examples include `LGREP_WORKTREE_DEDUP`, explicit warm paths, and timeout guidance for Vision/OpenCode usage. | pass | README.md and skills/lgrep/SKILL.md include `LGREP_WORKTREE_DEDUP`, explicit `LGREP_WARM_PATHS`, `LGREP_AUTO_WARM_DISK=false`, and `LGREP_TOOL_TIMEOUT_S` guidance. |
| AC5 | acceptance_criterion | Tool-selection guidance says: after one hybrid semantic timeout/deadline, retry once with `hybrid:false` and a small limit, then fall back to symbol/text/read. | pass | tests/test_tool_routing.py::TestPackagedInstructionRouting::test_packaged_instruction_retries_vector_only_after_semantic_timeout passed. |
| AC6 | acceptance_criterion | Tests cover the hybrid hot-path guard/fallback and timeout guidance. | pass | Focused suite passed 76 passed, 2 skipped; full suite passed 575 passed, 2 skipped; ruff passed. |
| C1 | constraint | Preserve existing semantic and symbol MCP tool names. | respected | No MCP tool names or public tool registrations changed; changes are storage/indexing/docs/tests only. |
| C2 | constraint | Preserve stdio/local defaults for general users. | respected | Docs preserve stdio/local defaults and present Vision tuning as agent-heavy setup guidance. |
| C3 | constraint | Do not add symlink/env hacks as substitutes for code-level latency control. | respected | No symlink/wrapper workaround introduced; code-level fallback and index-preparation changes were implemented. |
| C4 | constraint | Keep behavior structural: readiness state, config, tests; no prose-only fix. | respected | Behavior encoded in `ChunkStore.prepare_hybrid_indexes`, `search_hybrid` fallback, and regression tests. |
| DONT1 | avoidance | Do not mask all search failures as generic timeouts. | respected | Existing structured `ToolError` timeout remains; search failures are not globally masked. Hybrid missing-index case degrades specifically to vector results. |
| DONT2 | avoidance | Do not require users/agents to manually run indexing before first semantic search. | respected | First semantic search still auto-loads/auto-indexes; users are not required to manually index before searching. |
| DONT3 | avoidance | Do not make every cached repo warm automatically in this local setup. | respected | Vision/OpenCode docs and applied config use explicit warm paths plus `LGREP_AUTO_WARM_DISK=false`, not warm-all-caches. |

