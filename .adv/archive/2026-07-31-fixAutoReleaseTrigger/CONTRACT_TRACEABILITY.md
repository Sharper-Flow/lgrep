# Contract Traceability

**Change ID:** fixAutoReleaseTrigger
**Contract Version:** 1
**Rigor:** strict
**Reviewed:** 2026-07-31T20:23:42.270Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | Static workflow fixture tests verify valid main-push CI event selects the release job; invalid events produce detectable failure. Schema-validated YAML. Empirical post-merge proof pending. |
| AC2 | acceptance_criterion | pass | test | Tests verify checkout uses workflow_run.head_sha and tag is created before build. Wheel metadata derives from tag via Hatch VCS. |
| AC3 | acceptance_criterion | pass | test | Deploy command refuses non-trunk/dirty/untagged source; installs GitHub Release wheel; validates Vision config; restarts service. 23 unit tests cover safety paths. |
| AC4 | acceptance_criterion | pass | test | Reviewer verified live Vision prune_symbols and prune_orphans return refused_reason='' through real MCP transport with corrected Accept header. |
| AC5 | acceptance_criterion | pass | test | Deploy command implements one bounded initialization retry; persistent failure exits nonzero with step-specific output. Unit tests cover retry and failure paths. |
| AC6 | acceptance_criterion | pass | test | Deploy command exits nonzero on any unhealthy step; worktree dry-run correctly refused. Full suite 800 passed. |
| C1 | constraint | respected | static_check | Tag-before-build preserved; workflow tags head_sha before python -m build. |
| C2 | constraint | respected | static_check | Deploy command refuses worktree branches; all code work in ADV worktree. |
| C3 | constraint | respected | static_check | Health checks use dry_run=true only; no destructive operations. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-211aea8c7d3d | AC1, AC2 |  | C1 |  |
| tk-5ad7d7b8cd00 | AC3, AC4, AC5, AC6 |  | C2, C3 |  |
