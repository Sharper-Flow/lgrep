# Contract Traceability

**Change ID:** fixDeployedLgrepDefects
**Contract Version:** 1
**Rigor:** strict
**Reviewed:** 2026-07-31T05:48:12.834Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | FastMCP structured-output regression and final suite pass. |
| AC2 | acceptance_criterion | pass | test | Registered tool response boundary coverage passed. |
| AC3 | acceptance_criterion | pass | test | Tag-derived version tests and CLI tag parity passed. |
| AC4 | acceptance_criterion | pass | test | Index window convergence tests passed. |
| AC5 | acceptance_criterion | pass | test | Cancellation and background continuation tests passed. |
| AC6 | acceptance_criterion | pass | test | Partial hybrid preparation and never-indexed staleness tests passed. |
| AC7 | acceptance_criterion | pass | test | Final verification: 761 tests passed; Ruff clean. |
| C1 | constraint | respected | static_check | Dry-run and destructive grant logic retained and tested. |
| C2 | constraint | respected | static_check | Bounded worker and cooperative cancellation paths retained. |
| C3 | constraint | respected | static_check | All code work occurred in the ADV worktree; no deployment ran. |
| OOS1 | out_of_scope | not_applicable | not_applicable | MCP server architecture was not replaced. |
| OOS2 | out_of_scope | not_applicable | not_applicable | Default index budget was not raised. |
| OOS3 | out_of_scope | not_applicable | not_applicable | No unrelated token/performance redesign was included. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-9b4b0557204d | AC1, AC2 |  | C1 |  |
| tk-d52e12ed9629 | AC3 |  | C3 |  |
| tk-c4d22c86c341 | AC4, AC5, AC6 |  | C2, OOS2 |  |
| tk-fa1b3d3830cb |  | AC1, AC2, AC3, AC4, AC5, AC6, AC7 | C1, C2, C3 |  |
