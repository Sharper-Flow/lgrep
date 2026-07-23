# Contract Traceability

**Change ID:** addSkipStalenessReindexFlag
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-23T16:12:00.000Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| SC1 | success_criterion | pass | review | Full suite 662 passed; test_stale_search_does_not_await_reindex asserts search returns current results without awaiting index_all. commits a247ae4, dfc42b7. |
| SC2 | success_criterion | pass | review | test_background_reindex_dedupes_concurrent_stale_searches + test_background_reindex_refreshes_next_search assert search never blocks on reindex and freshness converges. |
| AC1 | acceptance_criterion | pass | test | _execute_search (tools_semantic.py:191) calls await _schedule_background_reindex and serves current index; no index_all awaited. Verified in code + test_stale_search_does_not_await_reindex. |
| AC2 | acceptance_criterion | pass | test | _schedule_background_reindex triggers non-awaited single-flight refresh; dedup via _indexing_events + _bg_reindex_tasks (race fixed dfc42b7). test_background_reindex_dedupes + refresh_next_search. |
| AC3 | acceptance_criterion | pass | test | No silent-freeze: staleness always triggers background refresh regardless of watcher config (rq-search-never-blocks-on-reindex.3); test_background_reindex_failure_leaves_index_stale_and_serves. |
| AC4 | acceptance_criterion | pass | test | Full suite 662 passed; test_warm_path_does_not_call_index_all, test_check_staleness_deadline_returns_fresh, cancellation tests preserved; warm path + search_symbols untouched. |
| AC5 | acceptance_criterion | pass | test | rq-search-never-blocks-on-reindex added to docs/specs/lgrepSemanticCacheLifecycle.md (v1.1.0); README behavior note rewritten. daemon .adv amend deferred (camelCase tooling gap) — substance captured in new requirement body + README. |
| C1 | constraint | respected | static_check | Warm path, search_symbols, and freshness mechanism unchanged; change is additive (background refresh replaces blocking await). No regression to existing callers. |
| C2 | constraint | respected | static_check | Reuses _auto_index_project_single_flight + bounded RuntimeSupervisor (run_blocking); no unbounded fanout introduced. |
| C3 | constraint | respected | static_check | Background index_all remains cancellable + bounded by LGREP_INDEX_MAX_WALL_S; test_background_reindex_cancelled_on_shutdown verifies terminal RuntimeJob state. |
| C4 | constraint | respected | static_check | Warm path and search_symbols code paths untouched (diff confirms). |
| C5 | constraint | respected | static_check | LanceDB storage unchanged; no embedding model or index format change. |
| DONT1 | avoidance | respected | review | No LGREP_SKIP_STALENESS_REINDEX (or any sync-fresh) flag shipped; env table unchanged. |
| DONT2 | avoidance | respected | review | LGREP_AUTO_WATCH framed as optimization only; background refresh handles drift without watcher (AC2/AC3). |
| DONT3 | avoidance | respected | review | No vision repo code changes; fix is lgrep server-side only. |
| DONT4 | avoidance | respected | review | Vision request_timeout not modified. |
| DONT5 | avoidance | respected | review | LGREP_TOOL_TIMEOUT_S unchanged. |
| DONT6 | avoidance | respected | review | Warm path not made non-blocking; untouched (deferred separate issue). |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-3bdaac324d11 | AC2, C2 |  | C3 |  |
| tk-b195233ff137 | AC5 | AC5 |  |  |
| tk-4e5d4ad5db2e | AC4 |  | C3 |  |
| tk-a484560f4ff4 | AC1, AC3 | SC1, SC2 |  |  |
| tk-93db4c0f05d2 |  | AC4, SC1, SC2 |  |  |
| tk-7ac3aa0e2d76 | AC5 |  | DONT1 |  |
