# Contract Traceability

**Change ID:** fixDaemonSafety
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-05-27T22:00:21.754Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | tests/test_daemon_runtime.py covers timeout/cancellation abandonment and eventual terminal states; full pytest passed 597 tests. |
| AC2 | acceptance_criterion | pass | test | tests/test_server.py bounded global status test asserts no-arg status does not call DB count methods and returns summary_only entries; full pytest passed. |
| AC3 | acceptance_criterion | pass | test | tests/test_diagnostics.py covers diagnostics shape, loaded project paths, active/recent jobs, abandonment summary, and secret/env exclusion; full pytest passed. |
| AC4 | acceptance_criterion | pass | test | tests/test_server.py, tests/test_watcher.py, tests/test_daemon_runtime.py verify semantic/index/status/watcher/maintenance blocking work routes through RuntimeSupervisor; full pytest and ruff passed. |
| AC5 | acceptance_criterion | pass | test | tests/test_worktree_cache.py, tests/test_prune_orphans.py, and MCP prune transport tests passed in full pytest; destructive shared HTTP prune remains forced dry-run. |
| AC6 | acceptance_criterion | pass | test | Full pytest passed 597 tests including tests/test_daemon_runtime.py, bounded global status tests, tests/test_diagnostics.py, and tests/test_conflict_markers.py. |
| AC7 | acceptance_criterion | pass | test | README.md, skills/lgrep/SKILL.md, and src/lgrep/install_opencode.py updated; docs-focused tests passed. |
| AC8 | acceptance_criterion | pass | test | CHANGELOG.md conflict markers removed under tk-83b6a4a2bfd0; tests/test_conflict_markers.py passed in full pytest. |
| C1 | constraint | respected | static_check | Existing tool names preserved; lgrep_diagnostics added as additive MCP tool. Status response kept legacy fields and added optional summary_only/detail fields. |
| C2 | constraint | respected | static_check | RuntimeSupervisor, JobStatus enum, TypedDict response contracts, contract tests, and deterministic pytest coverage own correctness. |
| C3 | constraint | respected | static_check | Worktree cache and prune suites passed; invalidation and prune safety code routes through runtime without changing deletion invariants. |
| C4 | constraint | respected | static_check | tests/test_diagnostics.py asserts diagnostics exclude VOYAGE_API_KEY and env values; full pytest passed. |
| C5 | constraint | respected | static_check | Shared HTTP prune dry-run test passed; full prune/worktree suites passed. |
| C6 | constraint | respected | static_check | Diff is limited to daemon runtime supervision, diagnostics, status, watcher/maintenance routing, docs, and tests. |
| DONT1 | avoidance | respected | review | Reviewer verdict READY; diagnostics are explicit RuntimeSupervisor state, not heuristic process-name/thread-count inference. |
| DONT2 | avoidance | respected | review | No-arg status test asserts DB count/get_indexed_files are not called. |
| DONT3 | avoidance | respected | review | Worktree cache/prune suites passed; no silent cache deletion behavior added. |
| DONT4 | avoidance | respected | review | All work ran in isolated change worktree; no live Vision-managed daemon restart or destructive live cache cleanup was performed. |
| DONT5 | avoidance | respected | review | Changed surface remains daemon safety and operator guidance only. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-158349582402 | AC1, AC2, AC3, AC4, AC5, AC6, AC7 |  | C1, C2, C3, C4, C5, C6, DONT1, DONT2, DONT3, DONT4, DONT5 |  |
| tk-a1e4017119bb | AC1, AC3, AC4 | AC1, AC4 | C1, C2, C4, DONT1, DONT4 |  |
| tk-477891c5555a | AC1, AC2, AC4 | AC1, AC2, AC4 | C1, C2, C3, C4, C5, DONT1, DONT2, DONT3 |  |
| tk-6f319f8a3370 | AC1, AC3, AC4 | AC3 | C1, C2, C4, DONT1, DONT4 |  |
| tk-8f61c34f4fd8 | AC1, AC4, AC5 | AC5 | C3, C5, DONT2, DONT3, DONT4 |  |
| tk-83b6a4a2bfd0 | AC6, AC8 | AC6, AC8 | C1, C2 |  |
| tk-e91fbaeb6a94 | AC7 | AC7 | C1, C4, C6, DONT4, DONT5 |  |
| tk-b3a825d40742 |  | AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8 | C1, C2, C3, C4, C5, C6, DONT1, DONT2, DONT3, DONT4, DONT5 |  |
