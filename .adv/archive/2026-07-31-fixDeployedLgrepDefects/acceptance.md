# Acceptance

Reviewed at: 2026-07-31T05:48:12.834Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | **Validated maintenance responses** — `prune_orphans`, `prune_symbols`, and `invalidate_worktree_cache` each return a successful structured MCP response for their non-destructive/default invocation. Their `_meta` envelope validates against one canonical declared schema; no response relies on omitted required properties. | pass | FastMCP structured-output regression and final suite pass. |
| AC2 | acceptance_criterion | **Whole-surface contract coverage** — an automated test invokes tools through FastMCP's structured-output path and fails if a registered tool returns data that violates its declared response schema. It covers the maintenance tools at minimum. | pass | Registered tool response boundary coverage passed. |
| AC3 | acceptance_criterion | **Single release identity** — for a release tag `vX.Y.Z`, the built distribution metadata and `lgrep --version` are exactly `X.Y.Z`; the release workflow cannot build an artifact before resolving that release version. | pass | Tag-derived version tests and CLI tag parity passed. |
| AC4 | acceptance_criterion | **Bounded index convergence** — under a deterministic budget that forces multiple windows, a repository reaches 100% of its pending files indexed in finite bounded windows. Each window remains bounded by `LGREP_INDEX_MAX_WALL_S`. | pass | Index window convergence tests passed. |
| AC5 | acceptance_criterion | **Search and cancellation safety** — normal search does not wait for background reindexing; explicit cancellation still releases the bounded worker and does not schedule uncontrolled retries. | pass | Cancellation and background continuation tests passed. |
| AC6 | acceptance_criterion | **Partial index quality** — after any completed index window, the usable index is prepared for hybrid search; never-indexed files are detected as pending rather than hidden by a partial timestamp. | pass | Partial hybrid preparation and never-indexed staleness tests passed. |
| AC7 | acceptance_criterion | **Regression quality** — full project tests and Ruff pass without new warnings/errors attributable to this change. | pass | Final verification: 761 tests passed; Ruff clean. |
| C1 | constraint | Preserve dry-run defaults and destructive-operation grants. | respected | Dry-run and destructive grant logic retained and tested. |
| C2 | constraint | Preserve bounded worker count, cooperative cancellation, and tool timeout safeguards. | respected | Bounded worker and cooperative cancellation paths retained. |
| C3 | constraint | Do not deploy or rebuild from a worktree; release/deployment occurs only after merge from current trunk. | respected | All code work occurred in the ADV worktree; no deployment ran. |
| OOS1 | out_of_scope | Replacing the existing MCP server or indexing architecture. | not_applicable | MCP server architecture was not replaced. |
| OOS2 | out_of_scope | Increasing the global index budget merely to move the failure threshold. | not_applicable | Default index budget was not raised. |
| OOS3 | out_of_scope | Unrelated token-usage/performance redesigns. | not_applicable | No unrelated token/performance redesign was included. |

