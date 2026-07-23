# Archive Briefing Digest

**Change ID:** addSkipStalenessReindexFlag
**Title:** Add skip-staleness-reindex flag
**Status:** archived
**Generated:** 2026-07-23T16:30:26.419Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY

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

Showing 30 of 30 durable facts.

- **[archive_only_evidence]** decisions: Implemented _on_bg_reindex_done to log {'error': ...} return values from _auto_index_project_single_flight as bg_reindex_failed — The existing single-flight helper swallows index_all failures and returns an error dict; without this, background failures would be silently logged as success.
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_server.py::TestBackgroundReindex -v --tb=short (1) — RED: ImportError for _schedule_background_reindex (expected)
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_server.py::TestBackgroundReindex -v --tb=short (0) — GREEN: test_background_reindex_dedupes_concurrent_stale_searches, test_background_reindex_failure_leaves_index_stale_and_serves pass
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/ --tb=short (0) — 662 passed, 16 warnings in 18.88s
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part1-red-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part1-green-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- **[archive_only_evidence]** decisions: Added a bounded active-job drain loop in _shutdown after gather returns — asyncio.gather waits for the task coroutine to finish, but abandoned ThreadPoolExecutor futures update RuntimeJob terminal status via a done callback that may fire afterwards; polling active jobs ensures terminal state is observable before runtime.shutdown().
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_runtime_cancellation.py::test_background_reindex_cancelled_on_shutdown -v --tb=short (1) — RED: _shutdown did not cancel/await background task (expected)
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_runtime_cancellation.py::test_background_reindex_cancelled_on_shutdown -v --tb=short (0) — GREEN: test_background_reindex_cancelled_on_shutdown passes
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/ --tb=short (0) — 662 passed, 16 warnings in 18.88s
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part3-red-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part3-green-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- **[archive_only_evidence]** decisions: Updated existing TestStalenessPreflight tests to patch lifecycle._auto_index_project_single_flight and wait for _bg_reindex_tasks — _execute_search now routes through _schedule_background_reindex in lifecycle.py, so the patch target and timing needed to change to keep the assertions meaningful.
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_server.py::TestBackgroundReindex::test_stale_search_does_not_await_reindex tests/test_server.py::TestBackgroundReindex::test_background_reindex_refreshes_next_search -v --tb=short (1) — RED: assertions failed because _execute_search still awaited _auto_index_project_single_flight (expected)
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/test_server.py::TestBackgroundReindex::test_stale_search_does_not_await_reindex tests/test_server.py::TestBackgroundReindex::test_background_reindex_refreshes_next_search -v --tb=short (0) — GREEN: test_stale_search_does_not_await_reindex, test_background_reindex_refreshes_next_search pass
- **[archive_only_evidence]** verification: uv run --extra dev pytest tests/ --tb=short (0) — 662 passed, 16 warnings in 18.88s
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part2-red-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: part2-green-001
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- **[report_follow_up]** follow_ups: Done callback must branch on task.cancelled() before calling result()/exception(); retrieve exceptions in try/except so asyncio emits no unretrieved-exception warning. A returned {'error': ...} must be logged as a failed refresh.
- **[report_follow_up]** follow_ups: Existing stale tests at tests/test_server.py:1547-1621 assert synchronous invocation counts; adjust them to await the registered background task deterministically before asserting counts.
- **[report_follow_up]** follow_ups: Re-fire suppression holds after scheduler correction: _indexing_events lasts until _auto_index_project_single_flight finally calls _finish_single_flight_indexing at lifecycle.py:493-494, while timestamp refresh occurs at 432-439. A race can only create a follower, not a duplicate index_all.
- **[research_citation]** sources: Local lifecycle single-flight and shutdown: Existing shutdown does not cancel tasks; single-flight owns its event under the asyncio lock, submits index_all via RuntimeSupervisor, refreshes latest_indexed_at before the finally block clears the event. (src/lgrep/server/lifecycle.py:137-159,374-494)
- **[research_citation]** sources: Local runtime supervisor: run_blocking creates structured jobs in the bounded executor and only signals cooperative cancellation when its awaiting coroutine is cancelled; shutdown marks jobs and uses wait=False. (src/lgrep/server/runtime.py:119-170,184-196,235-269)
- **[research_citation]** sources: Python asyncio documentation: create_task schedules on the running loop and requires a strong task reference; cancellation must be observed/awaited for cleanup, and Task.exception raises CancelledError for a cancelled task. (https://docs.python.org/3/library/asyncio-task.html)
- **[archive_only_evidence]** architecture_assessment: Default stale-serving plus background refresh is sound and simpler than an environment flag. The design needs two material corrections: its helper sample is syntactically invalid because a regular def contains async with, and shutdown must await cancelled background tasks before shutting down the runtime so cooperative cancellation is delivered and observed.
- **[unresolved_action]** validation.blockers: MAJOR: proposed normal def _schedule_background_reindex contains async with, which cannot compile. Making it async requires awaiting it from _execute_search, contrary to stated synchronous non-blocking call shape.
- **[unresolved_action]** validation.blockers: MAJOR: cancellation without awaiting the cancelled background tasks before runtime shutdown does not ensure run_blocking sees cancellation, signals index_all's cancel_event, or reaches terminal job state.

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| SC1 | success_criterion | pass |
| SC2 | success_criterion | pass |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| C4 | constraint | respected |
| C5 | constraint | respected |
| DONT1 | avoidance | respected |
| DONT2 | avoidance | respected |
| DONT3 | avoidance | respected |
| DONT4 | avoidance | respected |
| DONT5 | avoidance | respected |
| DONT6 | avoidance | respected |

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: part1-red-001
- verification_missing: No durable adv_run_test evidence found for run_id: part1-green-001
- verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- verification_missing: No durable adv_run_test evidence found for run_id: part3-red-001
- verification_missing: No durable adv_run_test evidence found for run_id: part3-green-001
- verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- verification_missing: No durable adv_run_test evidence found for run_id: part2-red-001
- verification_missing: No durable adv_run_test evidence found for run_id: part2-green-001
- verification_missing: No durable adv_run_test evidence found for run_id: full-suite-662
- MAJOR: proposed normal def _schedule_background_reindex contains async with, which cannot compile. Making it async requires awaiting it from _execute_search, contrary to stated synchronous non-blocking call shape.
- MAJOR: cancellation without awaiting the cancelled background tasks before runtime shutdown does not ensure run_blocking sees cancellation, signals index_all's cancel_event, or reaches terminal job state.
