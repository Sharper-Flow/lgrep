# Contract Traceability

**Change ID:** gateRemainingDestructiveMcp
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-30T20:31:53.472Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | Registry tests pin four destructiveHint tools; full 736 suite pass. |
| AC2 | acceptance_criterion | pass | test | No-grant tests prove preview/no-op and no deletion. |
| AC3 | acceptance_criterion | pass | test | Grant tests prove isolated real deletion for all four. |
| AC4 | acceptance_criterion | pass | test | Invalidation messages name grant/no CLI equivalent. |
| AC5 | acceptance_criterion | pass | test | Worktree description truthfully states conditional delete/grant. |
| AC6 | acceptance_criterion | pass | test | Diagnostics preserved; no runtime LGREP_TRANSPORT environment access. |
| AC7 | acceptance_criterion | pass | test | 736 suite and ruff pass; managed proof release-time. |
| C1 | constraint | respected | static_check | Additive response fields. |
| C2 | constraint | respected | static_check | Handler-only; core/CLI unchanged. |
| C3 | constraint | respected | static_check | Temporary isolated fixtures. |
| C4 | constraint | respected | static_check | Prune semantics retained. |
| C5 | constraint | respected | static_check | SDK/Vision unchanged. |
| OOS1 | out_of_scope | not_applicable | not_applicable | No proxy auth. |
| OOS2 | out_of_scope | not_applicable | not_applicable | No invalidation CLI. |
| OOS3 | out_of_scope | not_applicable | not_applicable | No candidate lookup change. |
| OOS4 | out_of_scope | not_applicable | not_applicable | No broad IndexStore redesign. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-dfa0f990b25c | AC2, AC3, AC4, AC5 |  | C1, C2, C3, C4, C5, OOS1, OOS2, OOS3, OOS4 |  |
| tk-389d9063d15c | AC1, AC6 |  | C1, C2, C3, C4, C5, OOS1, OOS2, OOS3, OOS4 |  |
| tk-7d4722205f39 |  | AC1, AC2, AC3, AC4, AC5, AC6, AC7 | C1, C2, C3, C4, C5, OOS1, OOS2, OOS3, OOS4 |  |
