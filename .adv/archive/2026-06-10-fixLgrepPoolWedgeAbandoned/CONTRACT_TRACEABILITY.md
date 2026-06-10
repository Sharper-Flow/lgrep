# Contract Traceability

**Change ID:** fixLgrepPoolWedgeAbandoned
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-06-10T20:06:06.888Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | test_index_all_raises_on_cancel_event + test_index_all_raises_on_mid_loop_cancel PASS. index_all checks cancel_event.is_set() at per-file loop top, raises OperationCancelled. |
| AC2 | acceptance_criterion | pass | test | test_run_blocking_sets_cancel_event_on_cancellation PASS. run_blocking calls cancel_event.set() before _mark_cancelled_or_abandoned + raise on asyncio.CancelledError (runtime.py). |
| AC3 | acceptance_criterion | pass | test | test_check_staleness_deadline_returns_fresh PASS. _check_staleness bounded by LGREP_STALENESS_DEADLINE_S (4.0s), returns (False,0)+logs on deadline. |
| AC4 | acceptance_criterion | pass | test | AMENDED (reality-matched, user-approved). Live probe: pokeedge (2157 files) cold search 8s-timed-out (expected) but did NOT permanently wedge — abandoned index_all job reached terminal state and freed its worker. Core permanent-wedge bug (v1 failure) resolved. Reviewer also fixed a propagation gap (lifecycle now passes cancel_event INTO index_all via lambda, commit b6db72a) so cooperative checks fire in the live path too. |
| AC5 | acceptance_criterion | pass | test | AMENDED (<=60s wall-clock bound, user-approved). Live probe diagnostics: job-00000002 index_all reached terminal status=failed_after_abandon, error='OperationCancelled: index_all wall-clock budget exceeded', duration_ms=60054; failed_after_abandon_count=1; worker freed. active_job_count returns to 0 after in-flight indexes terminate. Sub-30s deferred to OOS8. |
| AC6 | acceptance_criterion | pass | test | tests/test_runtime_cancellation.py: 9 tests cover AC1,AC2,AC3,AC8,AC9,AC10 + rq-daemon-cancel01.3. Failed against v1, pass with v2. |
| AC7 | acceptance_criterion | pass | test | adv_run_test post-remediation: uv run pytest = 606 passed, 2 skipped, 0 failed; uv run ruff check src tests = All checks passed. |
| AC8 | acceptance_criterion | pass | test | test_embed_documents_raises_between_batches_on_cancel + test_embed_batch_retry_aborts_wait_immediately_on_cancel PASS. embed_documents checks per-batch; _embed_batch_with_retry uses cancel_event.wait(timeout=delay) replacing time.sleep — aborts <0.9s vs ~31s worst case. |
| AC9 | acceptance_criterion | pass | test | test_index_file_raises_before_embed_on_cancel + test_index_file_raises_before_storage_on_cancel PASS. index_file raises OperationCancelled before embed and before storage. |
| AC10 | acceptance_criterion | pass | test | test_index_all_raises_on_wall_clock_budget PASS. index_all reads LGREP_INDEX_MAX_WALL_S (60s), raises OperationCancelled + index_all_wall_clock_exceeded log when exceeded. Verified live (job terminated at 60054ms). |
| SC1 | success_criterion | pass | review | Warm-index searches return fast; cold-repo first-call timeout is now bounded (terminal via wall-clock) not a permanent wedge. Core reliability objective met. |
| SC2 | success_criterion | pass | review | active_job_count for index_all returns to 0 after in-flight indexes terminate (verified live: job reached failed_after_abandon, freed worker) — no forever-accumulating abandoned jobs. |
| SC3 | success_criterion | pass | review | Abandoned index threads now terminate (wall-clock + cooperative cancel) rather than accumulating — bounds thread/worker growth. v1's unbounded orphan-thread buildup resolved. |
| C1 | constraint | respected | static_check | lgrepDaemonOperationalSafety honored + strengthened: rq-daemon-cancel01.2 (FINISHED/FAILED_AFTER_ABANDON) now genuinely reachable because worker thread exits; 01.1/01.3 preserved; executor01.1/.2 owned bounded supervisor unchanged. Reviewer confirmed. |
| C2 | constraint | respected | static_check | Works at LGREP_WORKER_MAX_THREADS=2 (live probe ran at that default). No servers.yaml change. |
| C3 | constraint | respected | static_check | Public MCP surface unchanged. index_all/index_file/embed_documents/_embed_batch_with_retry/run_blocking gain optional cancel_event kwargs only. |
| C4 | constraint | respected | static_check | No disk cache format change. Runtime cancellation + staleness/wall-clock deadline logic + docs only. |
| DONT1 | avoidance | respected | review | Bounded executor retained; no process-pool/asyncio-pool replacement. Work made cooperatively cancellable. |
| DONT2 | avoidance | respected | review | LGREP_WORKTREE_DEDUP semantics untouched; stale-cleanup branch unchanged. |
| DONT3 | avoidance | respected | review | LGREP_TOOL_TIMEOUT_S not tuned. New LGREP_STALENESS_DEADLINE_S + LGREP_INDEX_MAX_WALL_S bound work within the existing 8s budget rather than raising it. |
| DONT4 | avoidance | respected | review | LGREP_WORKER_MAX_THREADS not bumped; no vision servers.yaml change in diff. |
| OOS1 | out_of_scope | not_applicable | not_applicable | No process-pool replacement. |
| OOS2 | out_of_scope | not_applicable | not_applicable | No asyncio-native LanceDB swap. |
| OOS3 | out_of_scope | not_applicable | not_applicable | No CLI changes. |
| OOS4 | out_of_scope | not_applicable | not_applicable | No vision servers.yaml changes. |
| OOS5 | out_of_scope | not_applicable | not_applicable | Single-flight coordination unchanged; only cancel_event wiring added (in-scope per AC1/AC2/AC9). |
| OOS6 | out_of_scope | not_applicable | not_applicable | No disk cache format changes. |
| OOS7 | out_of_scope | not_applicable | not_applicable | No new spec; brings impl into compliance with existing lgrepDaemonOperationalSafety. |
| OOS8 | out_of_scope | not_applicable | not_applicable | Voyage client.embed() HTTP timeout deliberately deferred. Surfaced as follow-up: required for sub-30s abandoned-job termination on huge cold repos. |
| OOS9 | out_of_scope | not_applicable | not_applicable | index_semantic tool + watcher cancel wiring deferred (P25 related-scan). Reviewer confirmed correctly scoped out, not silently broken. |
| OOS10 | out_of_scope | not_applicable | not_applicable | Background-index redesign deferred — the deeper fix for cold-repo search latency. Surfaced as follow-up. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-45441be6f257 | AC1, AC2, AC3, AC6 | AC1, AC2, AC3 | C1 |  |
| tk-cd9d5fa9cb07 | AC1 | AC1 | DONT1, DONT2, C3 |  |
| tk-dafdc284c4ca | AC2 | AC2 | C1, C2 |  |
| tk-f9aa510faf46 | AC1, AC2, AC5 | AC1, AC5 | C1 |  |
| tk-3e97da692b6b | AC3 | AC3 | C4 |  |
| tk-a66cd1a02904 | SC1 |  |  |  |
| tk-98253123e32d | AC7 | AC6, AC7 | C4 |  |
| tk-4ff98c9e1cae | AC4 | AC4, SC1, SC2, SC3 |  |  |
| tk-473f56d4aeac | AC5 | AC4, AC5 |  |  |
| tk-7e160f0d3360 | AC8, AC9, AC10, AC6 | AC8, AC9, AC10 | C1 |  |
| tk-2f2785109a4a | AC8, AC9 |  | C3 |  |
| tk-36ec2822a2a2 | AC8 | AC8 | C3, DONT1 |  |
| tk-5c91b82a7c04 | AC9 | AC9 | C3, DONT2 |  |
| tk-d738b1eb0c9a | AC10 | AC10 | C2, DONT3 |  |
| tk-14cc20156f84 | AC7 | AC6, AC7 | C4 |  |
| tk-66e44de89437 | AC4, AC5 | AC4, AC5, SC1, SC2, SC3 | C2, DONT4 |  |
