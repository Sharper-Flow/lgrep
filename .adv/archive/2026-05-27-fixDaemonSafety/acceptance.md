# Acceptance

Reviewed at: 2026-05-27T22:00:21.754Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | Timed-out/cancelled expensive daemon jobs are visible in job state as cancelled/abandoned/finished; never stuck “active” forever. | pass | tests/test_daemon_runtime.py covers timeout/cancellation abandonment and eventual terminal states; full pytest passed 597 tests. |
| AC2 | acceptance_criterion | `lgrep_status_semantic(path="")` returns cheap bounded summary by default and avoids deep LanceDB per-project counts unless explicitly scoped/deep. | pass | tests/test_server.py bounded global status test asserts no-arg status does not call DB count methods and returns summary_only entries; full pytest passed. |
| AC3 | acceptance_criterion | Diagnostics expose PID/uptime, loaded projects, active/recent jobs, job kind, full local project path, age, and terminal state; no secrets/env values. | pass | tests/test_diagnostics.py covers diagnostics shape, loaded project paths, active/recent jobs, abandonment summary, and secret/env exclusion; full pytest passed. |
| AC4 | acceptance_criterion | Blocking sync work uses bounded runtime supervision, not unbounded default-executor fanout. | pass | tests/test_server.py, tests/test_watcher.py, tests/test_daemon_runtime.py verify semantic/index/status/watcher/maintenance blocking work routes through RuntimeSupervisor; full pytest and ruff passed. |
| AC5 | acceptance_criterion | Shared cache/worktree safety remains intact; no silent chunk deletion/corruption and prune transport guards stay enforced. | pass | tests/test_worktree_cache.py, tests/test_prune_orphans.py, and MCP prune transport tests passed in full pytest; destructive shared HTTP prune remains forced dry-run. |
| AC6 | acceptance_criterion | Tests cover timeout/abandonment state, bounded global status, diagnostics shape, and conflict-marker rejection. | pass | Full pytest passed 597 tests including tests/test_daemon_runtime.py, bounded global status tests, tests/test_diagnostics.py, and tests/test_conflict_markers.py. |
| AC7 | acceptance_criterion | Docs explain Vision/shared HTTP safe settings and high-CPU/thread troubleshooting. | pass | README.md, skills/lgrep/SKILL.md, and src/lgrep/install_opencode.py updated; docs-focused tests passed. |
| AC8 | acceptance_criterion | `CHANGELOG.md` conflict markers are removed. | pass | CHANGELOG.md conflict markers removed under tk-83b6a4a2bfd0; tests/test_conflict_markers.py passed in full pytest. |
| C1 | constraint | Preserve existing MCP tool names and response compatibility unless design explicitly justifies an additive or versioned change. | respected | Existing tool names preserved; lgrep_diagnostics added as additive MCP tool. Status response kept legacy fields and added optional summary_only/detail fields. |
| C2 | constraint | Prefer structural correctness: job registry, typed response contracts, bounded executors, deterministic tests, and explicit runtime state over log scraping or heuristic inference. | respected | RuntimeSupervisor, JobStatus enum, TypedDict response contracts, contract tests, and deterministic pytest coverage own correctness. |
| C3 | constraint | Keep shared-cache safety invariants from `lgrepSemanticCacheLifecycle`; do not delete or mutate live cache data implicitly. | respected | Worktree cache and prune suites passed; invalidation and prune safety code routes through runtime without changing deletion invariants. |
| C4 | constraint | Do not expose secrets, environment values, or API keys in diagnostics. | respected | tests/test_diagnostics.py asserts diagnostics exclude VOYAGE_API_KEY and env values; full pytest passed. |
| C5 | constraint | Do not weaken prune transport-safety guards. | respected | Shared HTTP prune dry-run test passed; full prune/worktree suites passed. |
| C6 | constraint | Avoid product novelty before daemon stability; graph/local-embedding ideas remain out of scope. | respected | Diff is limited to daemon runtime supervision, diagnostics, status, watcher/maintenance routing, docs, and tests. |
| DONT1 | avoidance | Do not make correctness depend on heuristic process-name/thread-count guessing alone. | respected | Reviewer verdict READY; diagnostics are explicit RuntimeSupervisor state, not heuristic process-name/thread-count inference. |
| DONT2 | avoidance | Do not let global status trigger unbounded per-project disk/LanceDB work by default. | respected | No-arg status test asserts DB count/get_indexed_files are not called. |
| DONT3 | avoidance | Do not silently discard or corrupt shared worktree cache data while improving cleanup/visibility. | respected | Worktree cache/prune suites passed; no silent cache deletion behavior added. |
| DONT4 | avoidance | Do not restart or destructively clean the live Vision-managed daemon/cache as part of implementation without explicit operator action. | respected | All work ran in isolated change worktree; no live Vision-managed daemon restart or destructive live cache cleanup was performed. |
| DONT5 | avoidance | Do not broaden into graph/code-impact features, local embedding backend migration, or public internet deployment platform work. | respected | Changed surface remains daemon safety and operator guidance only. |

