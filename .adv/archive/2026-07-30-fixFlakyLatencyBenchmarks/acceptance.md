# Acceptance

Reviewed at: 2026-07-30T20:28:00.359Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | **AC1 — Honest aggregation:** both latency benchmarks use `statistics.median` over exactly 10 measured calls. No truncated index selection, percentile claim, or max-based variable/message remains. | pass | Both runtime benchmarks use Python stdlib statistics.median; focused 9 passed tr_ms7xd4dm_1ffb7331. |
| AC2 | acceptance_criterion | **AC2 — One outlier does not decide CI:** a pure aggregation test over `[0.25, 0.3, 0.3, 0.35, 0.35, 0.4, 0.4, 0.45, 0.5, 159.0]` returns median `0.375`ms and satisfies the 50ms symbol-search budget. | pass | One 159ms outlier among nine low samples yields median 0.375 via pytest.approx and passes strict 50ms budget. |
| AC3 | acceptance_criterion | **AC3 — Uniform regression still fails:** a pure aggregation test over ten `60.0`ms samples returns median `60.0`ms and fails the 50ms symbol-search budget. | pass | Ten 60ms samples yield 60ms and fail strict 50ms budget. |
| AC4 | acceptance_criterion | **AC4 — Both paths covered:** semantic search keeps a strict median budget under 100ms; symbol search keeps a strict median budget under 50ms. The two existing runtime benchmarks each use the same aggregation helper. | pass | Ten calls retained on each path; semantic <100ms and symbol <50ms median boundaries pure-tested. |
| AC5 | acceptance_criterion | **AC5 — Hang guard:** every individual measured call remains below 1000ms. This is explicitly a hang/deadlock guard, not tail-latency coverage. | pass | Per-sample 1000ms hang guard has strict 999.9/1000 boundary proof. |
| AC6 | acceptance_criterion | **AC6 — CI recovery:** the full test suite, ruff check, and ruff format check pass locally; after merge the GitHub CI matrix passes on Python 3.11, 3.12, and 3.13. | pass | Focused 9 passed tr_ms7xd4dm_1ffb7331; independent full suite 724 passed; ruff clean; post-merge CI remains release proof. |
| C1 | constraint | Test-only change: do not modify `src/lgrep/` behavior. | respected | Only tests/test_benchmark_latency.py changed. |
| C2 | constraint | Keep 10 measured calls per benchmark; no suite-runtime increase for a larger sample set. | respected | Exactly ten calls remain per runtime benchmark. |
| C3 | constraint | Do not delete, skip, or budget-inflate either benchmark. | respected | Existing strict 100/50 budgets retained; no skipped/deleted benchmark. |
| C4 | constraint | Use Python standard library only; project requires Python 3.11+. | respected | Python stdlib statistics only; project is Python 3.11+. |
| OOS1 | out_of_scope | A tail-latency SLA. That needs dedicated profiling/load tooling, not a shared-runner unit test. | not_applicable | No tail-SLA claim; hang guard is deadlock only. |
| OOS2 | out_of_scope | Changing CI runners or job topology. | not_applicable | CI topology unchanged. |
| OOS3 | out_of_scope | Altering the measured search implementations. | not_applicable | No source implementation change. |

