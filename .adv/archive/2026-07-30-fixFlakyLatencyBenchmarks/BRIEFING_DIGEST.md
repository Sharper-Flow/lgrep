# Archive Briefing Digest

**Change ID:** fixFlakyLatencyBenchmarks
**Title:** Fix flaky latency benchmarks
**Status:** archived
**Generated:** 2026-07-30T20:34:23.659Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY
- Origin: discovery

## Archive Digest

**Status:** archived

| Gate | Status |
| --- | --- |
| proposal | done |
| discovery | done |
| design | done |
| planning | done |
| execution | done |
| acceptance | done |
| release | pending |

## Epic Context

No Epic membership

## Durable Facts

Showing 46 of 46 durable facts.

- **[archive_only_evidence]** decisions: Implemented a private _median helper instead of importing statistics.median — Keeps the test file self-contained with no new runtime or test dependencies; matches contract wording 'private statistics.median helper'.
- **[archive_only_evidence]** decisions: Replaced percentile indexing with sorted median average of middle two values — A true median is robust to a single outlier and removes the flaky pseudo-p95 calculation that indexed past the 95th percentile on only 10 samples.
- **[archive_only_evidence]** decisions: Added a per-sample PER_SAMPLE_HANG_GUARD_MS = 1000.0 assertion inside each measured loop — Honest hang/deadlock guard that fails fast on any individual catastrophic sample without confusing it with the median budget.
- **[archive_only_evidence]** decisions: Retained strict <100ms semantic and <50ms symbol budgets and kept exactly 10 measured calls per benchmark — Contract forbids budget increases or test count changes; only the aggregation method changes.
- **[archive_only_evidence]** decisions: Used pytest.approx only where the median is a floating-point average — Avoids fragile exact float comparisons for the (0 + 0.75)/2 and (5 + 6)/2 cases while still asserting exact values for integer medians.
- **[archive_only_evidence]** decisions: Reordered hang-guard assertions to satisfy SIM300 Yoda-condition lint — Preserved the boundary-test semantics (guard > sample) while keeping ruff clean without noqa suppression.
- **[archive_only_evidence]** verification: uv run pytest tests/test_benchmark_latency.py -v (1) — Red phase: 8 tests collected, failures driven by NotImplementedError in the unimplemented _median helper; pure tests and benchmark median assertions failed as expected.
- **[archive_only_evidence]** verification: uv run pytest tests/test_benchmark_latency.py -v (0) — Green phase: all 8 tests pass (2 runtime benchmarks + 5 pure median/budget tests + index_folder benchmark).
- **[archive_only_evidence]** verification: uv run ruff check tests/test_benchmark_latency.py && uv run ruff format --check tests/test_benchmark_latency.py (0) — Ruff lint and format checks pass on changed file.
- **[archive_only_evidence]** verification: uv run pytest tests/test_benchmark_latency.py -v (0) — Verify phase: all 8 tests still pass after lint-fix edits.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vmhay_8da6ddd1
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vmzgs_3b635702
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vno8j_1af96cda
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vnz6r_b1a4deea
- **[archive_only_evidence]** decisions: Imported and used statistics.median instead of a custom helper — Contract explicitly requires stdlib statistics.median to structurally eliminate hand-rolled index-arithmetic defects.
- **[archive_only_evidence]** decisions: Removed the private _median helper entirely — The helper was the source of the contract violation; runtime benchmarks and pure tests now call statistics.median directly.
- **[archive_only_evidence]** decisions: Updated pure tests to call statistics.median directly — Ensures pure tests exercise the exact same stdlib behavior used by the runtime benchmarks, without reimplementing median logic.
- **[archive_only_evidence]** decisions: Fixed typo 'roust' to 'robust' in test name and docstring — Spelling correctness requested by review.
- **[archive_only_evidence]** decisions: Retained all other contract elements (budgets, hang guard, 10 calls, no skips/deps) — Only the median implementation mechanism changed; everything else stayed within the approved design.
- **[archive_only_evidence]** verification: uv run pytest tests/test_benchmark_latency.py -v (0) — All 8 tests pass: 2 runtime median benchmarks + index_folder benchmark + 5 pure statistics.median/budget tests.
- **[archive_only_evidence]** verification: uv run ruff check tests/test_benchmark_latency.py && uv run ruff format --check tests/test_benchmark_latency.py (0) — Ruff lint and format checks pass on changed file.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7w3qff_30432a32
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7w3ilz_8eea1eab
- **[unresolved_action]** required_main_agent_actions: Create the required task checkpoint for the in-scope reviewer test change before marking the task complete.
- **[archive_only_evidence]** changes_made: tests/test_benchmark_latency.py: Expanded the pure strict-median-boundary test to exercise equality and just-below behavior for both the 100ms semantic and 50ms symbol budgets.
- **[archive_only_evidence]** verification: tests_run=uv run pytest tests/test_benchmark_latency.py, git diff --check origin/main...HEAD, git diff --check results=pass — Focused benchmark suite collected 9 tests and passed in 4.31s. Both diff whitespace checks passed. `bin/oc-test` is absent; direct `python -m pytest` lacks pytest, so the repository's uv environment was used.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run pytest tests/test_benchmark_latency.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check origin/main...HEAD
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
- **[report_follow_up]** follow_ups: When budget constant names are renamed from P95 to MEDIAN, also update the module docstring lines 10-11 ('asserts p95 latency') to match, or reviewers will re-flag the mislabel.
- **[report_follow_up]** follow_ups: If a real tail-latency (p95/p99) SLA becomes a product requirement later, add a dedicated profiling benchmark with n>=40 — do not retrofit percentiles onto this 10-sample CI unit test.
- **[report_follow_up]** follow_ups: The optional 1000ms hang-ceiling should be tuned against future CI if any stall is observed near it; 159ms observed gives ~6x margin but is based on only 2 data points.
- **[research_citation]** sources: CPython statistics.median source + docs: median(data) sorts and returns middle value; for even count returns mean of the two middle values (n//2-1, n//2). Robust central location; less affected by outliers. No NaN handling. Raises StatisticsError on empty. (https://github.com/python/cpython/blob/main/Lib/statistics.py (median) and Doc/library/statistics.rst)
- **[research_citation]** sources: CPython statistics.quantiles docs: quantiles(data, n=100, method='inclusive'|'exclusive'). inclusive: i-th sorted point maps to quantile (i-1)/(m-1); exclusive: i/(m+1), both linear-interpolate. Docs warn 'For meaningful results, number of data points should exceed n' — with n=100 and only 10 samples, p95 is ill-defined/coarse. (https://github.com/python/cpython/blob/main/Doc/library/statistics.rst)
- **[research_citation]** sources: lgrep pyproject.toml: requires-python = '>=3.11'. statistics.quantiles (added 3.8) and median are guaranteed available. No statistics.trimmed_mean exists in stdlib (confirmed via docs inventory: mean/fmean/median/median_low/median_high/harmonic_mean/mode/quantiles/stdev/variance — no trimmed mean). (/home/jon/dev/lgrep/pyproject.toml)
- **[research_citation]** sources.omitted: 2 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The aggregation is fundamentally wrong: latencies[int(len(latencies)*0.95)] on 10 samples is index 9 = the max, so the test asserts 'max < budget' while calling it p95. Max has a 0% breakdown point — any single scheduler stall fails it (exactly the two CI failures). No percentile computed from 10 samples is robust: inclusive p95 still interpolates toward the max (verified 87.7ms > 50ms budget for the CI scenario), and exclusive p95 extrapolates BEYOND the max to the outlier value (159ms). The math-only correction (compute a real p95) does NOT fix flakiness — confirming the task's proven fact. The structurally correct fix is to aggregate central tendency with the maximum breakdown point: statistics.median. Median of 10 = mean of 5th and 6th sorted values, so a single outlier (top half) has zero effect; it requires 6+ of 10 samples to move (50% breakdown point). A uniform code regression moves every sample, so the median moves with it and trips the budget. This separates '1/10 slow' (scheduler noise, ignore) from '10/10 slow' (real regression, catch). median is a single stdlib call with no index arithmetic, eliminating the off-by-one class that caused the original bug.
- **[report_follow_up]** follow_ups: WARNING: packet omitted explicit IN_SCOPE/OUT_OF_SCOPE/DONE_WHEN/STOP_WHEN/VERIFICATION anchors; scope was inferred from the prompt body. Continue using prompt scope.
- **[report_follow_up]** follow_ups: Engineering: confirm AC2 0.375 assertion uses pytest.approx (could not execute Python in this read-only validation environment to confirm exact IEEE-754 rounding of (0.35+0.4)/2; tolerance is safe either way).
- **[report_follow_up]** follow_ups: Engineering: note test_index_folder_latency (single-shot 5s, non-aggregating) is correctly out of scope - design/contract could state this explicitly to avoid confusion about 'two runtime benchmarks'.
- **[report_follow_up]** follow_ups: Optional very-low-severity coverage asymmetry: no pure test asserts a uniform regression failing the 100ms semantic budget (AC3/AC2 exercise only the 50ms symbol budget); runtime semantic test covers the real path.
- **[research_citation]** sources: Repo: actual flaky test file: Current 'p95' is int(9.5)=9 -> the max of 10 sorted samples. One scheduler stall (outlier) decides CI. This is the confirmed flakiness root cause. (tests/test_benchmark_latency.py (lines 96-97, 162-163: p95 = latencies[int(len(latencies)*0.95)] == latencies[9] == max))
- **[research_citation]** sources: Repo: Python version requirements: Project requires Python 3.11+; statistics.median (stdlib since 3.4) is guaranteed available on all CI matrix versions. Constraint C4 satisfied. Pure tests use stdlib only. (pyproject.toml (requires-python >=3.11; classifiers 3.11/3.12/3.13; tool.ruff target-version py311; asyncio_mode auto))
- **[research_citation]** sources: Python stdlib docs: statistics.median: median sorts then averages the two middle order statistics for even n. No percentile-index arithmetic to get wrong. Correct robust estimator for n=10. (https://docs.python.org/3/library/statistics.html#statistics.median)
- **[research_citation]** sources.omitted: 2 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The design correctly replaces a hand-rolled pseudo-percentile that is actually the max (int(len*0.95)==latencies[9] for n=10) with statistics.median, a stdlib robust estimator with a 50% breakdown point. The defect root cause is confirmed against the real code: a single shared-runner scheduler stall becomes the max and fails CI, exactly the reported flakiness. Median over n=10 ignores up to 4 high outliers (the 5th breaks it = 50% breakdown), so one stall cannot decide the result, while a uniform regression shifts every sample and still trips the existing 50ms/100ms budgets (AC3). The separate 1000ms-per-sample hang guard is a thoughtful compensating control: switching from max to median loses single-outlier detection, and the guard restores catastrophic/hang detection without re-flagging ordinary outliers. Constraints are respected: test-only (C1), 10 calls retained (C2), budgets unchanged 100/50 with no skip/inflate (C3), stdlib median on 3.11+ (C4). All six ACs are addressable by the planned test set and the arithmetic is verified. Deviation vs the by-the-book robust-statistics approach: NONE. The honest scoping (tail-latency SLA is out of scope, OOS1) is appropriate for a shared-runner unit test.

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| AC6 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| C4 | constraint | respected |
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |
| OOS3 | out_of_scope | not_applicable |

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vmhay_8da6ddd1
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vmzgs_3b635702
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vno8j_1af96cda
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7vnz6r_b1a4deea
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7w3qff_30432a32
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7w3ilz_8eea1eab
- Create the required task checkpoint for the in-scope reviewer test change before marking the task complete.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run pytest tests/test_benchmark_latency.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check origin/main...HEAD
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
