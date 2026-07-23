# Executive Summary — addSkipStalenessReindexFlag

## Outcome
`lgrep.search_semantic` no longer times out when routed through the Vision MCP proxy. Previously, the first search against a stale index blocked on a minutes-long full Voyage re-embed, blowing past Vision's 30s `request_timeout` (`provider_timeout`). The search path now serves the current index immediately and refreshes freshness in the background, so every search returns fast and the next search after drift is fresh.

## Why it matters
This was the root cause of `search_semantic` failing through the proxy while `search_symbols` worked — a daily-friction defect for agent-driven code search. The fix is a default behavior change (not an opt-in flag), so it resolves the failure mode for all deployments without operator configuration.

## What changed
- `_execute_search` (tools_semantic.py) no longer awaits a full `index_all`; it schedules a background single-flight refresh and serves the current index.
- New `_schedule_background_reindex` + `_bg_reindex_tasks` registry + `_on_bg_reindex_done` callback (lifecycle.py) — reuses the existing bounded `RuntimeSupervisor` and `_auto_index_project_single_flight` leader/follower coordinator.
- `_shutdown` cancels and awaits outstanding background reindex tasks before tearing down the runtime, guaranteeing terminal job state.
- Spec: new `rq-search-never-blocks-on-reindex` (lgrepSemanticCacheLifecycle, 4 GWT scenarios). README behavior note rewritten.

## Verification
- Full test suite: **662 passed** (3 independent runs).
- 5 new tests cover non-awaiting search, concurrent-search dedup, background-failure isolation, next-search freshness convergence, and shutdown cancellation reaching terminal `RuntimeJob` state.
- Preserved guardrails: `test_warm_path_does_not_call_index_all`, `LGREP_STALENESS_DEADLINE_S` deadline test, cancellation tests. Warm path + `search_symbols` untouched.
- Independent acceptance review (adv-reviewer): verdict READY; found and fixed a scheduler race (concurrent schedulers spawning untracked follower tasks) — fix verified.

## Risks / follow-ups
- **First-after-drift search serves slightly stale results** (by design; the `OperationCancelled` path already established this precedent). Freshness converges on the next search.
- **Tooling gap (non-blocking):** the `lgrepDaemonOperationalSafety` `rq-daemon-shared-mode01.1` `.adv`-store amend could not be applied — `adv_delta_*` reject camelCase capability keys. Substance is captured in the new requirement body + README. Follow-up: camelCase→kebab capability migration or delta-tooling fix.
- No env flag shipped; no Vision code change; no embedding model/index format change.