# Contract Traceability

**Change ID:** fixFlakyLatencyBenchmarks
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-30T20:28:00.359Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | Both runtime benchmarks use Python stdlib statistics.median; focused 9 passed tr_ms7xd4dm_1ffb7331. |
| AC2 | acceptance_criterion | pass | test | One 159ms outlier among nine low samples yields median 0.375 via pytest.approx and passes strict 50ms budget. |
| AC3 | acceptance_criterion | pass | test | Ten 60ms samples yield 60ms and fail strict 50ms budget. |
| AC4 | acceptance_criterion | pass | test | Ten calls retained on each path; semantic <100ms and symbol <50ms median boundaries pure-tested. |
| AC5 | acceptance_criterion | pass | test | Per-sample 1000ms hang guard has strict 999.9/1000 boundary proof. |
| AC6 | acceptance_criterion | pass | test | Focused 9 passed tr_ms7xd4dm_1ffb7331; independent full suite 724 passed; ruff clean; post-merge CI remains release proof. |
| C1 | constraint | respected | static_check | Only tests/test_benchmark_latency.py changed. |
| C2 | constraint | respected | static_check | Exactly ten calls remain per runtime benchmark. |
| C3 | constraint | respected | static_check | Existing strict 100/50 budgets retained; no skipped/deleted benchmark. |
| C4 | constraint | respected | static_check | Python stdlib statistics only; project is Python 3.11+. |
| OOS1 | out_of_scope | not_applicable | not_applicable | No tail-SLA claim; hang guard is deadlock only. |
| OOS2 | out_of_scope | not_applicable | not_applicable | CI topology unchanged. |
| OOS3 | out_of_scope | not_applicable | not_applicable | No source implementation change. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-b439c00f4e14 | AC1, AC2, AC3, AC4, AC5 |  | C1, C2, C3, C4, OOS1, OOS2, OOS3 |  |
| tk-0edb2ec14795 |  | AC1, AC2, AC3, AC4, AC5, AC6 | C1, C2, C3, C4, OOS1, OOS2, OOS3 |  |
