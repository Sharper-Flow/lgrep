# Design

## Architecture Overview

Add a small runtime-supervision layer owned by `LgrepContext` and used by daemon paths that submit expensive blocking work. The supervisor is the structural source of truth for job lifecycle, bounded executor use, cancellation/abandonment state, and cheap diagnostics.

Core pieces:

- `src/lgrep/server/runtime.py` — new local module with typed job models and `RuntimeSupervisor`.
- `LgrepContext.runtime` — owns one bounded `ThreadPoolExecutor`, active job map, recent terminal job history, and startup timestamp.
- `RuntimeSupervisor.run_blocking(...)` — single path for expensive sync work from semantic tools, status deep checks, maintenance calls, and watcher callbacks.
- `lgrep_status_semantic(path="")` — cheap memory-only global summary by default. Scoped path status remains the deep count path. No all-project deep flag is needed until a concrete caller exists.
- `lgrep_diagnostics` — new read-only typed MCP tool for PID/uptime, loaded projects/aliases, active/recent jobs, executor configuration, and recent timeout/abandonment state.
- Tests and docs become the guardrail: timeout abandonment, bounded no-arg status, diagnostics shape, executor limit/config, conflict-marker rejection, Vision/shared HTTP troubleshooting.

This keeps correctness structural: runtime state is tracked in typed in-process records, not inferred from process CPU, thread names, logs, or support-ticket prose.

## Key Decisions

### 1. Add a `RuntimeSupervisor` instead of relying on `asyncio.to_thread`

`asyncio.wait_for` currently bounds the coroutine but does not structurally guarantee that a blocking sync function already running in a worker thread has stopped. The supervisor will:

- allocate a stable job id for each expensive blocking call
- record kind, project path, caller/tool, created/started/finished timestamps, status, and error summary
- submit work to a bounded `ThreadPoolExecutor`
- on coroutine cancellation/timeout, mark the job `abandoned` or `cancel_requested`
- attach a future callback so the underlying sync call later transitions to `finished_after_abandon` or `failed_after_abandon`
- retain bounded recent history for diagnostics

Job statuses should be a small enum, for example: `queued`, `running`, `finished`, `failed`, `cancel_requested`, `abandoned`, `finished_after_abandon`, `failed_after_abandon`.

This matches the user decision: timed-out sync work is marked abandoned/observed, not killed unsafely.

### 2. Bound blocking concurrency with an owned executor

Introduce `LGREP_WORKER_MAX_THREADS` with a conservative default of `4` and create the executor in `_startup` through `LgrepContext`. Add `shutdown(cancel_futures=True)` in `_shutdown` so queued work is cancelled and running work is not silently orphaned from runtime state.

Rationale:

- The current default executor can grow without lgrep-specific visibility.
- A named executor makes diagnostics meaningful.
- `cancel_futures=True` cancels pending jobs; running sync calls may still complete, so job state must distinguish pending cancellation from unsafe kill.

### 3. Keep global status cheap by default

Change no-arg `status_semantic` behavior:

- default no-arg path returns `projects` from in-memory `app_ctx.projects` only
- do not call `state.db.count_chunks` / `state.db.get_indexed_files` for each loaded project by default
- include additive fields such as `summary`, `stats_source`, `deep_stats_omitted`, or `_meta` as needed for clarity
- keep required legacy fields (`files`, `chunks`, `watching`, `project`, `disk_cache`, `error`) populated from cached memory values or safe defaults
- require `status_semantic(path="/repo")` for exact deep file/chunk counts

This satisfies cheap default behavior while keeping a bounded path for operators who need exact counts.

### 4. Add a dedicated diagnostics tool

Add a read-only MCP tool, exposed as `lgrep_diagnostics`, returning a typed response with:

- `pid`
- `uptime_seconds`
- `transport`
- `worker_max_threads`
- `active_job_count`
- `recent_job_count`
- `loaded_project_count`
- `loaded_projects` with full local paths, watching, alias/canonical hints if available
- active jobs: id, kind, project, caller, status, age_ms, started_at
- recent jobs: id, kind, project, caller, status, duration_ms, abandoned flag, error summary
- recent timeout/abandonment summary

Diagnostics must not include environment variables, `VOYAGE_API_KEY`, request payload secrets, or raw exception tracebacks in the response. Logs may keep exception details, but support-facing diagnostics should use bounded summaries.

### 5. Route same-pattern expensive work through the supervisor

Initial owned scope:

- semantic search staleness check and LanceDB search calls
- explicit semantic index calls
- auto-index single-flight leader work
- project stats/deep status calls
- startup orphan sweep
- maintenance tools that run sync cache cleanup/invalidation
- watcher index/delete callbacks

Symbol tools also use `asyncio.to_thread`. Route them through the supervisor where straightforward; if a symbol-tool conversion expands risk, document the staged remainder during planning while ensuring all acceptance-critical daemon paths are supervised.

### 6. Unify timeout decorator ownership

Keep one canonical `TOOL_TIMEOUT_S` / `time_tool` implementation. Preferred locality is `src/lgrep/server/responses.py` because it already owns `ToolError`, TypedDict response shapes, and structured timeout error creation. `src/lgrep/server/__init__.py` can re-export the canonical decorator for existing imports.

Timeout and executor errors should be classified structurally:

- `timeout`
- `executor_rejected`
- `sync_exception`
- `cancelled_or_abandoned`

This replaces misleading generic wording such as “may need re-indexing or Voyage slow” for non-search surfaces.

### 7. Spec ownership

Create a new capability spec `lgrepDaemonOperationalSafety`. Existing `lgrepSemanticCacheLifecycle` remains owner for prune/cache safety; new daemon spec owns runtime job lifecycle, bounded status, diagnostics, and executor behavior.

## ADR Drafts

No ADR draft is required. The design is important but not hard to reverse at architecture-record level: the supervisor is an internal module, additive diagnostics are reversible, and the main tradeoffs are already captured in the agreement/design artifacts.

## Implementation Strategy

1. **Contract tests first**
   - Add tests proving no-arg global status does not call `_get_project_stats` / deep DB methods by default.
   - Add tests for diagnostics shape and secret exclusion.
   - Add tests around supervisor timeout/cancellation behavior using controllable blocking functions/futures.
   - Add conflict-marker guard test or CI script and first prove it catches current `CHANGELOG.md` markers.

2. **Runtime supervisor module**
   - Add typed dataclasses/TypedDicts/enums for job status and diagnostic output.
   - Implement bounded executor and history pruning.
   - Implement `run_blocking(kind, project, caller, fn, *args, **kwargs)`.
   - Ensure cancellation path marks abandoned and future callback observes final completion.
   - Emit structured error classifications for timeout, rejection, cancellation/abandonment, and sync exceptions.

3. **Lifecycle integration**
   - Add `runtime` and `started_at` to `LgrepContext`.
   - Initialize runtime in `_startup`.
   - Shut down runtime in `_shutdown` after watcher stop and before context teardown.

4. **Semantic/status integration**
   - Replace `asyncio.to_thread` in semantic search/index/status critical paths with `app_ctx.runtime.run_blocking`.
   - Make global no-arg status memory-only by default.
   - Keep single-project status behavior as the deep exact-count path.

5. **Diagnostics tool**
   - Add response TypedDicts in `responses.py`.
   - Add tool implementation in `tools_diagnostics.py`; import it in server package for decorator registration.
   - Export function from `src/lgrep/server/__init__.py`.

6. **Watcher/maintenance routing**
   - Pass runtime into `FileWatcher` / `IndexingHandler` or provide a safe optional supervisor path.
   - Route startup sweep and cache maintenance through runtime where they run in daemon context.

7. **Docs and hygiene**
   - Resolve `CHANGELOG.md` markers.
   - Add conflict-marker guard to tests/CI.
   - Update README, `instructions/lgrep-tools.md`, `skills/lgrep/SKILL.md`, and installer guidance for Vision/shared HTTP safe mode and diagnostics.

8. **Spec deltas**
   - Add `lgrepDaemonOperationalSafety` spec with runtime job, cancellation/abandonment, global status, diagnostics, executor, shared-mode docs, and conflict-marker requirements.
   - If cache alias visibility is implemented in diagnostics, add a small delta to `lgrepSemanticCacheLifecycle` only for visibility/reporting, not destructive behavior.

## LBP Analysis

This design follows long-term best practice for local daemon infrastructure:

- **Structural state over heuristics:** job state lives in typed records; diagnostics read state directly.
- **Bounded work over default executor fanout:** lgrep owns concurrency limits instead of relying on Python's process-wide default executor behavior.
- **Cheap health endpoints:** no-arg status becomes safe to call during incidents.
- **Additive compatibility:** existing tool names remain; global status preserves legacy top-level shape while adding metadata.
- **Safe cancellation semantics:** Python cannot reliably kill arbitrary running sync work in a thread; marking abandoned and observing completion is honest and safe.
- **Spec-law reinforcement:** new daemon-safety requirements prevent future regressions in operational behavior.

## Affected Components

- `src/lgrep/server/runtime.py` — new runtime supervisor.
- `src/lgrep/server/lifecycle.py` — context ownership, startup/shutdown, auto-index, stats, warming/sweep.
- `src/lgrep/server/tools_semantic.py` — search/index/status integration and cheap global status.
- `src/lgrep/server/tools_diagnostics.py` — diagnostics tool.
- `src/lgrep/server/tools_maintenance.py` — supervised maintenance calls.
- `src/lgrep/server/responses.py` — diagnostic response contracts, canonical timeout helper, optional status metadata fields.
- `src/lgrep/server/__init__.py` — imports/re-exports and tool registration.
- `src/lgrep/watcher.py` — supervised watcher index/delete work.
- `src/lgrep/storage/_chunk_store.py` — likely no core changes; LanceDB timeout support remains an implementation check before adding query-level timeouts.
- `tests/` — runtime/status/diagnostics/conflict-marker coverage.
- `README.md`, `instructions/lgrep-tools.md`, `skills/lgrep/SKILL.md`, `src/lgrep/install_opencode.py` — safe shared-mode guidance.
- `.adv/specs/` — new daemon operational-safety capability and possibly cache lifecycle visibility delta.
- `CHANGELOG.md` — conflict cleanup.

## Risks / Mitigations

| Risk | Mitigation |
|---|---|
| Running sync functions cannot be killed safely after timeout | Represent abandonment structurally; observe final completion through future callbacks; do not claim killed. |
| New diagnostics leaks secrets | Only include explicit allowlisted fields; never include env vars, API keys, raw request context, or full tracebacks. |
| Global status compatibility break | Preserve `projects` list and required fields; add metadata, keep exact counts scoped to a path. |
| Bounded executor queues too much work | Include queue/job counts and clear timeout/rejection behavior; keep tests around limit behavior. |
| Watcher integration becomes too invasive | Make supervisor optional in watcher constructor and route daemon-created watchers through it. |
| Spec scope gets too broad | New spec owns daemon runtime only; existing semantic cache lifecycle keeps cache safety. |
| Conflict-marker guard false positives | Scan tracked text/source files; exclude binary/cache/generated paths. |

## Validator Result

Validator: clean pass ✓

Independent validator verdict: `VALIDATED`.

Findings:

- Correctness: every agreement objective maps to a concrete design piece; abandonment model matches Python stdlib semantics.
- Simplicity: optional all-project `deep` flag was cautionary/YAGNI; design revised to keep no-arg status cheap-only and require scoped `path` for deep stats.
- Spec-law compliance: no contradiction with existing specs; new `lgrepDaemonOperationalSafety` is appropriately scoped.
- Alternatives: asyncio-only/TaskGroup and process-pool alternatives were considered and correctly rejected.

Validator recommendation incorporated: remove optional no-arg deep mode and add structured error classification for timeout/rejection/sync-exception paths.