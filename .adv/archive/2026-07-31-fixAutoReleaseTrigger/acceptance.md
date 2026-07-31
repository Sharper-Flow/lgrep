# Acceptance

Reviewed at: 2026-07-31T20:23:42.270Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | **Runnable release trigger** — a completed successful `CI` workflow on `main` creates an Auto Release run with at least one executable release job; zero-job outcomes are treated as failure evidence. | pass | Static workflow fixture tests verify valid main-push CI event selects the release job; invalid events produce detectable failure. Schema-validated YAML. Empirical post-merge proof pending. |
| AC2 | acceptance_criterion | **Exact release source** — Auto Release checks out `github.event.workflow_run.head_sha`, creates `vX.Y.Z` before building, and publishes wheel/sdist whose metadata is exactly `X.Y.Z`. | pass | Tests verify checkout uses workflow_run.head_sha and tag is created before build. Wheel metadata derives from tag via Hatch VCS. |
| AC3 | acceptance_criterion | **Safe local deploy command** — one documented command/script, executed only from clean merged `main`, installs the selected/tagged lgrep artifact into Vision's configured runtime, validates Vision configuration, and restarts the user service. | pass | Deploy command refuses non-trunk/dirty/untagged source; installs GitHub Release wheel; validates Vision config; restarts service. 23 unit tests cover safety paths. |
| AC4 | acceptance_criterion | **Real local health proof** — the deploy path confirms the installed CLI version equals the selected release and runs `prune_symbols` plus `prune_orphans` through Vision; both must return successful structured results with string `refused_reason`. | pass | Reviewer verified live Vision prune_symbols and prune_orphans return refused_reason='' through real MCP transport with corrected Accept header. |
| AC5 | acceptance_criterion | **Bounded startup handling** — one bounded initialization retry is allowed after a service restart; persistent initialization/health failure exits nonzero and reports the failing step. | pass | Deploy command implements one bounded initialization retry; persistent failure exits nonzero with step-specific output. Unit tests cover retry and failure paths. |
| AC6 | acceptance_criterion | **Release safety** — no local deploy claims healthy before all health checks pass; failed release runs or deployment checks are discoverable through CI output or command exit status. | pass | Deploy command exits nonzero on any unhealthy step; worktree dry-run correctly refused. Full suite 800 passed. |
| C1 | constraint | Preserve tag-before-build version derivation. | respected | Tag-before-build preserved; workflow tags head_sha before python -m build. |
| C2 | constraint | Do not deploy/rebuild from a worktree. | respected | Deploy command refuses worktree branches; all code work in ADV worktree. |
| C3 | constraint | Do not auto-delete caches or grant destructive maintenance operations. | respected | Health checks use dry_run=true only; no destructive operations. |

