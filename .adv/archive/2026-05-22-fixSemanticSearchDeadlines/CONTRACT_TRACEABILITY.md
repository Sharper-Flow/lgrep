# Contract Traceability

**Change ID:** fixSemanticSearchDeadlines
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | tests/test_storage.py::TestSearch::test_search_hybrid_does_not_create_indexes_on_query_path passed; reviewer confirmed `search_hybrid` no longer creates indexes on live query path. |
| AC2 | acceptance_criterion | pass | test | Vector fallback path exercised by storage test and worktree CLI probe against `/home/jon/dev/advance` returned vector results without timeout. |
| AC3 | acceptance_criterion | pass | test | `~/.config/vision/servers.yaml` was updated, validated, reloaded, and lgrep restarted with `LGREP_TOOL_TIMEOUT_S=8`; hybrid probe returned lgrep-owned structured timeout instead of Vision context deadline. |
| AC4 | acceptance_criterion | pass | test | README.md and skills/lgrep/SKILL.md include `LGREP_WORKTREE_DEDUP`, explicit `LGREP_WARM_PATHS`, `LGREP_AUTO_WARM_DISK=false`, and `LGREP_TOOL_TIMEOUT_S` guidance. |
| AC5 | acceptance_criterion | pass | test | tests/test_tool_routing.py::TestPackagedInstructionRouting::test_packaged_instruction_retries_vector_only_after_semantic_timeout passed. |
| AC6 | acceptance_criterion | pass | test | Focused suite passed 76 passed, 2 skipped; full suite passed 575 passed, 2 skipped; ruff passed. |
| C1 | constraint | respected | static_check | No MCP tool names or public tool registrations changed; changes are storage/indexing/docs/tests only. |
| C2 | constraint | respected | static_check | Docs preserve stdio/local defaults and present Vision tuning as agent-heavy setup guidance. |
| C3 | constraint | respected | static_check | No symlink/wrapper workaround introduced; code-level fallback and index-preparation changes were implemented. |
| C4 | constraint | respected | static_check | Behavior encoded in `ChunkStore.prepare_hybrid_indexes`, `search_hybrid` fallback, and regression tests. |
| DONT1 | avoidance | respected | review | Existing structured `ToolError` timeout remains; search failures are not globally masked. Hybrid missing-index case degrades specifically to vector results. |
| DONT2 | avoidance | respected | review | First semantic search still auto-loads/auto-indexes; users are not required to manually index before searching. |
| DONT3 | avoidance | respected | review | Vision/OpenCode docs and applied config use explicit warm paths plus `LGREP_AUTO_WARM_DISK=false`, not warm-all-caches. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-7c7b948984cb | AC6, C4 | AC1, AC2, AC5, AC6 | C1, C2, C3, DONT1, DONT2 |  |
| tk-b64c3b80d421 | AC1, AC2, C4 | AC1, AC2 | C1, C2, C3, DONT1, DONT2 |  |
| tk-9bd42b3be86a | AC3, AC4, AC5 | AC3, AC4, AC5 | C1, C2, C3, DONT3 |  |
| tk-58996ac72ca1 | AC6 | AC1, AC2, AC3, AC4, AC5, AC6 | C1, C2, C3, C4, DONT1, DONT2, DONT3 |  |
