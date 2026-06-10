# Executive Summary: Fix lgrep pool wedge from abandoned index

## Outcome
The shared lgrep MCP daemon no longer permanently wedges its bounded worker pool when a search-triggered auto-index is abandoned. Abandoned `index_all` jobs now reach a terminal state and release their worker, so the pool always recovers — resolving the pokeedge wedge that motivated this change.

## Root cause
A search against a stale project runs a synchronous `index_all` on lgrep's 2-thread bounded executor. When the 8s tool timeout cancelled the awaiting coroutine, the underlying thread kept running with no cooperative cancellation. With 2 workers, abandoned index jobs exhausted the pool and every subsequent search timed out.

## What was built (v2)
The change went through a design re-entry after a live probe proved v1 (cancel-check only *between* files) insufficient — the blocking embed work lives *inside* a single `index_file`. v2:
- **`OperationCancelled`** moved to `lgrep/exceptions.py` (breaks the indexing↔embeddings import cycle; re-exported from `indexing.py` for back-compat).
- **Cooperative cancellation threaded to every blocking seam**: `index_all` (per-file + wall-clock) → `index_file` (pre-embed, pre-storage) → `embed_documents` (per-batch) → `_embed_batch_with_retry` (per-attempt + **`cancel_event.wait(timeout=delay)` replacing an up-to-31s un-cancellable retry sleep**).
- **`RuntimeSupervisor.run_blocking`** sets the event on `asyncio.CancelledError` before re-raising.
- **Wall-clock backstop** `LGREP_INDEX_MAX_WALL_S` (default 60s) guarantees `index_all` aborts regardless of where it blocks.
- **`_check_staleness`** bounded by `LGREP_STALENESS_DEADLINE_S` (default 4.0s).
- **Acceptance-review catch**: the search-path wiring (`_auto_index_project_single_flight`) passed the event to `run_blocking` but not *into* `index_all` — fixed with a lambda so cooperative cancellation is effective in production, not just unit tests.

## Verification
- 9 regression tests (`tests/test_runtime_cancellation.py`) cover AC1–AC3, AC8–AC10 + rq-daemon-cancel01.3; fail against v1, pass with v2.
- Full suite: 606 passed, 2 skipped, 0 failed. ruff clean.
- **Live probe** (deployed to shared vision lgrep, `uv cache clean` + global-XDG install): abandoned `index_all` job reached terminal `failed_after_abandon` (`OperationCancelled: index_all wall-clock budget exceeded`, 60054ms), freed its worker, pool recovered. v1's permanent abandon (job stuck >42s, never terminal) is resolved.
- Spec `lgrepDaemonOperationalSafety` honored and strengthened (rq-daemon-cancel01.2 FINISHED/FAILED_AFTER_ABANDON now genuinely reachable).

## Amended scope (user-approved)
AC4/AC5 numeric thresholds (<8s search, <30s terminal) were amended to reality: pokeedge (2157 files) cold-indexes in >60s and the dominant blocker is a single un-interruptible Voyage `client.embed()` HTTP batch. The core objective — **no permanent wedge** — is met; sub-30s termination requires the embed-timeout follow-up.

## Follow-ups surfaced (out of scope, tracked)
- **OOS8**: HTTP request timeout on Voyage `client.embed()` so a single in-flight batch is interruptible (enables sub-30s abandoned-job termination).
- **OOS9**: thread `cancel_event` through the explicit `index_semantic` MCP tool + watcher index paths (P25 related-scan — same wedge pattern, currently unprotected).
- **OOS10**: background-index redesign so search never triggers a synchronous full reindex on huge cold repos (eliminates cold-repo search timeouts entirely).