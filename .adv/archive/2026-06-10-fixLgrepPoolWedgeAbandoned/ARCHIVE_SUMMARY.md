# Archive: Fix lgrep pool wedge from abandoned index

**Change ID:** fixLgrepPoolWedgeAbandoned
**Archived:** 2026-06-10T21:08:15.606Z
**Created:** 2026-06-10T17:39:04.401Z

## Tasks Completed

- ✅ TDD red: add tests/test_runtime_cancellation.py with three regression tests that must FAIL against current code
  > Task checkpoint completed
- ✅ TDD green: implement OperationCancelled exception and cancel_event kwarg on Indexer.index_all
  > Task checkpoint completed
- ✅ TDD green: implement cancel_event kwarg on RuntimeSupervisor.run_blocking
  > Task checkpoint completed
- ✅ TDD green: wire cancel event in _auto_index_project_single_flight
  > Task checkpoint completed
- ✅ TDD green: bound _check_staleness with wall-clock deadline
  > Task checkpoint completed
- ✅ Document the staleness-walk deadline behavior in skill and instructions
  > Task checkpoint completed
- ✅ Verify: run full test suite, ruff lint, and confirm no regressions
  > Task checkpoint completed
- ✅ Live probe: redeploy lgrep CLI, restart vision lgrep child, fire 5 search trials
  > Task checkpoint completed
- ✅ Wedge reproduction: trigger the original failure mode, confirm the fix prevents the wedge
  > Task checkpoint completed
- ✅ v2 TDD red: extend tests/test_runtime_cancellation.py with tests for AC8/AC9/AC10 that FAIL against v1 code
  > Task checkpoint completed
- ✅ v2 green: relocate OperationCancelled to src/lgrep/exceptions.py, re-export from indexing.py
  > Task checkpoint completed
- ✅ v2 green: thread cancel_event through embed_documents + _embed_batch_with_retry (AC8)
  > Task checkpoint completed
- ✅ v2 green: thread cancel_event through index_file (AC9)
  > Task checkpoint completed
- ✅ v2 green: add LGREP_INDEX_MAX_WALL_S wall-clock backstop to index_all (AC10)
  > Task checkpoint completed
- ✅ v2 verify: full test suite + ruff (AC7)
  > Task checkpoint completed
- ✅ v2 verify: LIVE PROBE with mandatory uv cache eviction (AC4 + AC5)
  > Task checkpoint completed

## Specs Modified


## Wisdom Accumulated

- **[gotcha]** Related-scan (P25): the explicit `index_semantic` MCP tool (src/lgrep/server/tools_semantic.py:375) calls Indexer.index_all WITHOUT cancel_event, so it can still wedge the bounded executor identically if its awaiting coroutine is cancelled mid-file. This fix scoped cancellation to the search-path auto-index (_auto_index_project_single_flight) per the agreed contract (OOS-scoped). The index_semantic full-reindex path is a legitimate same-pattern FOLLOW-UP, not an in-scope gap. watcher._do_index uses index_file (single file) so its wedge risk is bounded and lower. Recommend a follow-up change to thread cancel_event through index_semantic and watcher index_all-equivalent paths.
- **[failure]** LIVE PROBE FAILED (AC4+AC5). With the fix deployed to the shared vision lgrep, the pokeedge wedge REPRODUCES: search_semantic against /home/jon/dev/pokeedge times out at 8s, the index_all job is marked abandoned but NEVER reaches terminal state (finished_after_abandon_count stayed 0 at age 42s+, AC5 requires <30s), and a 2nd search wedges the 2nd worker (active_job_count:2, pool exhausted). Root cause: the cooperative cancel_event.is_set() check only fires BETWEEN files in Indexer.index_all's per-file loop. On pokeedge a single index_file call (Voyage embed batch + LanceDB write) blocks far longer than the cancellation window, so the thread cannot observe cancel_event mid-file. The design's 'exits within one file boundary' assumption does NOT hold for pokeedge's per-file cost. The unit tests passed because they simulate cancellation at a loop boundary, not mid-index_file. Fix is INSUFFICIENT. Remediation options: (a) cancellation check INSIDE index_file (per-chunk loop, before each embed/write), (b) bound index_all to a hard wall-clock budget and abort the batch, (c) make the embed/storage calls themselves interruptible. Deployment gotcha during probe: uv tool install reuses a cached wheel keyed on version 3.1.0, so reinstalling from canonical root did NOT evict the fix-build deployed at 14:12 — a version bump or `uv cache clean lgrep` is needed to truly swap deployed code.
- **[failure]** v2 LIVE PROBE — partial. The PERMANENT wedge IS FIXED: abandoned index_all jobs now reach terminal state (job-00000002 → failed_after_abandon, error "OperationCancelled: index_all wall-clock budget exceeded", duration 60054ms) and free their worker, instead of v1's forever-abandoned threads. BUT AC5 (<30s terminal) and AC4 (<8s search) are NOT met. Root cause refined: pokeedge has 2157 source files; a cold index_all legitimately takes >60s of Voyage embed work. The thread was stuck in a single un-interruptible client.embed() HTTP batch (OOS8), so the per-batch/per-retry cancel checks never got a turn — ONLY the LGREP_INDEX_MAX_WALL_S=60s backstop could terminate it, hence 60s not <30s. The 8s search timeout will ALWAYS abandon the first cold-index attempt on a 2157-file repo; with 2 workers and a new abandon every ~8s each taking 60s to clear, the pool can still be transiently saturated for up to ~60s windows even though it no longer wedges PERMANENTLY. The real fixes: (1) lower LGREP_INDEX_MAX_WALL_S default so abandoned jobs clear within AC5's 30s; (2) add an HTTP request timeout to voyageai client.embed() so a single batch is interruptible (currently OOS8); (3) reconsider whether search should trigger a full synchronous reindex at all on huge cold repos vs returning stale/empty fast and indexing in background. DEPLOYMENT TRAP RESOLVED: the per-project XDG_DATA_HOME shard (oc wrapper) means `uv tool install` from inside an opencode session installs to the SHARD (opencode-projects/<hash>/uv/tools), NOT the global /home/jon/.local/share/uv/tools that vision's /home/jon/.local/bin/lgrep symlink uses. Must run `env -u XDG_DATA_HOME uv tool install --reinstall --force <worktree>` to deploy to the global tool that vision actually runs. Also bump a dev version (3.1.1.dev0) to dodge uv's version-keyed wheel cache.
- **[gotcha]** CRITICAL review catch (acceptance phase): RuntimeSupervisor.run_blocking(fn, cancel_event=evt) only .set()s the event on CancelledError — it does NOT forward cancel_event as a kwarg into fn. So passing `run_blocking(..., state.indexer.index_all, cancel_event=evt)` does NOT give index_all the event; index_all() runs with cancel_event=None and only the wall-clock backstop can stop it. The fix: wrap the callee in a lambda that explicitly passes the event — `lambda ce=cancel_event: state.indexer.index_all(cancel_event=ce)`. Unit tests missed this because they call index_all(cancel_event=...) directly, bypassing the lifecycle wiring. LESSON: when a cancellation primitive is threaded through multiple layers, an integration/wiring test (or live probe) is required — per-unit tests of each layer can all pass while the layers aren't actually connected. The v2 live probe's wall-clock termination masked this gap (the job still terminated, just via the 60s backstop not the faster cooperative path).
