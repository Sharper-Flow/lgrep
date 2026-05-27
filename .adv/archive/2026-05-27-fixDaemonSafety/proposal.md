## Why

`lgrep` is strong as local code-intelligence infrastructure, but shared daemon mode is not yet operationally safe. In Vision/OpenCode shared subprocess use, it can sustain runaway CPU/thread load, global status can time out under stale/loaded project state, and operators lack cheap diagnostics to identify active jobs or stale cache pressure. Adjacent release/developer hygiene gaps — unresolved `CHANGELOG.md` conflict markers, missing daemon regression tests, and unclear shared-mode hardening docs/defaults — make the issue easier to ship or repeat.

## What Changes

- Add structural runtime supervision for expensive daemon work: bounded execution, active-job accounting, cancellation/abandonment handling, and shutdown cleanup where safe.
- Make global semantic status cheap and bounded by default; keep deep per-project disk/cache checks explicit and scoped.
- Add operator-facing diagnostics for shared daemon health: PID/uptime, loaded projects/aliases, active jobs by type/project/age, executor/thread/resource counters where practical, and recent timeout/error state.
- Harden shared HTTP/Vision defaults and docs so shared mode is an explicit scale-up path with resource limits, warm-path guidance, and troubleshooting.
- Add deterministic regression coverage for timeout/disconnect/global-status/job-supervision behavior.
- Resolve `CHANGELOG.md` conflict markers and add a CI/test guard against future conflict markers.

## Scope

### In Scope

- `src/lgrep/server/*` runtime lifecycle, status, diagnostics, timeout, and job-supervision behavior.
- `src/lgrep/watcher.py` and semantic indexing/search paths where they submit expensive work.
- `src/lgrep/tools/prune_orphans.py` / cache metadata surfaces only where needed for stale active-project or alias visibility.
- `README.md`, packaged lgrep instructions/skill docs, and installer/service guidance related to shared daemon hardening.
- Tests under `tests/` and CI guardrails needed to prove the above behavior.
- `CHANGELOG.md` conflict cleanup.
- Spec updates or new spec creation for daemon operational safety if discovery confirms no existing capability cleanly owns this behavior.

### Out of Scope

- New graph/code-impact product features.
- Switching from Voyage Code 3 to a local embedding backend.
- Public internet / enterprise deployment platform work beyond localhost/shared-daemon security and tuning guidance.
- Broad refactors unrelated to daemon safety, diagnostics, status cost, cache visibility, docs, and tests.
- Destructive cleanup of the user's live cache or restart of the live Vision-managed daemon as part of implementation without explicit operator action.

### Must Not

- Must not make correctness depend on heuristic process-name/thread-count guessing alone; runtime state must be tracked structurally in code.
- Must not let global status trigger unbounded per-project disk/LanceDB work by default.
- Must not silently discard or corrupt shared worktree cache data while improving cleanup/visibility.
- Must not expose secrets in diagnostics, logs, or support-oriented output.
- Must not weaken existing prune transport-safety guards.

## Success Criteria

1. Shared daemon expensive work is supervised by a bounded runtime mechanism with observable active-job state and safe cleanup/abandonment behavior on timeout, cancellation, or shutdown.
2. `lgrep_status_semantic(path="")` returns a cheap bounded response under many loaded projects and does not perform deep disk/LanceDB stats for every project by default.
3. A diagnostic/status surface identifies active indexing/search/status jobs with project, kind, age, and cancellation/timeout state without exposing secrets.
4. Regression tests cover timeout/disconnect-style cancellation or abandonment, global status cost bounding, active-job diagnostics, and executor/resource limits.
5. Shared HTTP/Vision documentation explains safe defaults (`LGREP_WARM_PATHS`, `LGREP_AUTO_WARM_DISK`, timeouts, worktree dedup, diagnostics) and troubleshooting for high CPU/thread incidents.
6. `CHANGELOG.md` contains no unresolved conflict markers, and CI or tests fail on future conflict markers.
7. Existing specs for semantic cache lifecycle and tool-selection behavior still pass; any new daemon-safety invariants are captured in spec form during design/acceptance.

## Acceptance Criteria

- A test or diagnostic fixture demonstrates that a timed-out or cancelled expensive daemon operation is no longer invisible: it is either cancelled safely or recorded as abandoned/finished in active-job state.
- A test demonstrates `lgrep_status_semantic(path="")` remains bounded with multiple loaded projects and does not call deep per-project LanceDB stats by default.
- A diagnostic/status response exposes active job kind, project, age, and terminal state for indexing/search/status work without returning secrets.
- CI or tests fail when unresolved conflict markers such as `<<<<<<<`, `=======`, or `>>>>>>>` are present in tracked text files.
- Documentation explains shared HTTP/Vision safe settings and the high-CPU/thread troubleshooting path.

## Affected Code

- `src/lgrep/server/lifecycle.py` — app context, startup/shutdown, warming, project state, single-flight indexing, stats.
- `src/lgrep/server/tools_semantic.py` — search/index/status/watch tool behavior.
- `src/lgrep/server/responses.py` and `src/lgrep/server/__init__.py` — response contracts, timeout behavior, potential duplicate timeout ownership.
- `src/lgrep/watcher.py` — file watcher executor submissions and incremental indexing/deletion jobs.
- `src/lgrep/indexing.py` and `src/lgrep/embeddings.py` — cancellation boundaries and long-running indexing/embed flows if needed.
- `src/lgrep/tools/prune_orphans.py` — stale alias/cache reporting only if discovery confirms needed.
- `README.md`, `instructions/lgrep-tools.md`, `skills/lgrep/SKILL.md`, `src/lgrep/install_opencode.py` — shared deployment and operational guidance.
- `tests/test_server.py`, `tests/test_worktree_cache.py`, `tests/test_prune_orphans.py`, and/or new focused tests.
- `.github/workflows/ci.yml` or test suite for conflict-marker guard.
- `CHANGELOG.md`.

## Related Repositories

- Current repo only: `Sharper-Flow/lgrep`.
- No product-linked repo scope configured in `project.json`.
- External systems involved operationally: Vision MCP manager and OpenCode, but no cross-repo implementation is planned unless discovery finds required config/docs updates outside this repo.

## Constraints

- Preserve existing MCP tool names and response compatibility unless discovery/design explicitly justifies a versioned or additive change.
- Prefer structural correctness: job registry, typed response contracts, bounded executors, deterministic tests, and explicit state over log scraping or heuristic inference.
- Keep shared-cache safety invariants from `lgrepSemanticCacheLifecycle`; do not delete or mutate live cache data implicitly.
- Avoid adding product novelty before daemon stability; graph/local-embedding ideas remain future discovery only.
- Do not create implementation tasks during proposal.

## Impact

- Operators can diagnose and recover shared-daemon high CPU/thread incidents without guessing.
- OpenCode/Vision users get safer defaults and clearer setup guidance for multi-session use.
- CI catches release hygiene regressions like conflict markers.
- The repo gains a clear operational-safety capability boundary for future changes.

## Context

Evidence sources:

- User-provided incident ticket: lgrep 3.1.0, Vision shared subprocess, sustained ~318-325% CPU for >1h, 140 threads, global status timeout, stale repo/worktree/temp paths.
- `docs/repo-improve-prep.md` updated 2026-05-27.
- Existing specs: `lgrepSemanticCacheLifecycle` and `lgrepToolSelectionOptimization`.
- Local evidence in `src/lgrep/server/*`, `src/lgrep/watcher.py`, `src/lgrep/indexing.py`, `tests/*`, `README.md`, and `CHANGELOG.md`.

## Discovery Findings

### Discovery Checklist

| Step | Status | Result |
|---|---|---|
| Origin validation | PASS | Local same-project discovery-origin change from `docs/repo-improve-prep.md`; no cross-project origin. |
| Skill Discovery | PASS | Loaded `lgrep`, `adv-arch-detection`, and `adv-improve`; `lgrep` and `adv-improve` directly matched. |
| Prior Research Extension | PASS | Extended `docs/repo-improve-prep.md`; new finding: LanceDB Python has recent timeout/cancellation work, so query timeout support must be checked during design. |
| Conflict & Related-Work Scan | PASS | `adv_change_list` found only this change; `adv_change_validate` passed with expected pre-prep warnings (`NO_TASKS`, `NO_DELTAS`); `adv_agenda_list` empty. |
| Edge Case Investigation | PASS | Edge cases captured for job supervision, status, diagnostics, cache safety, and CI hygiene. |
| Design Question Depth | PASS | Open design questions annotated with trust model, blast radius, and alternatives. |
| Draft Spec Delta Shapes | PASS | New daemon operational-safety spec deltas drafted below. |
| P25 Related-Pattern Scan | PASS | Similar `asyncio.to_thread`/executor patterns found across semantic, symbol, maintenance, lifecycle, and watcher surfaces. |
| LBP Check | PASS | Structural job registry + bounded executor + cheap health endpoint matches Python/FastMCP operational best practice. |

### Skills Considered

| Skill | Match | Action |
|---|---|---|
| `lgrep` | Direct: project/domain skill with shared Vision/OpenCode tuning guidance | Loaded; applied tool-selection and shared-daemon tuning context. |
| `adv-improve` | Direct: prior repo-wide improvement pack created this change | Loaded; reused evidence shape and synthesis. |
| `adv-arch-detection` | Partial: architecture/runtime-boundary correctness | Loaded; applied structural-correctness framing. |
| Other available ADV skills | Not core to daemon safety | Not loaded. |

### Extends

- `docs/repo-improve-prep.md` — cited Current State, LBP / Reference Comparison, Competitors & Alternatives, Emerging Patterns, and Applicability sections.
- New finding beyond pack: Exa result for LanceDB PR #2288 and issue #2898 indicates LanceDB Python query timeout/cancellation behavior has active/recent upstream work. Discovery/design must verify installed LanceDB API capabilities before wrapping search calls, rather than assuming sync cancellation stops underlying operations.
- No `temp/brainstorm-*.md` artifacts found.
- No archived ADV changes found in current repo scope.

### Conflict Scan

- Active/archived changes: only `fixDaemonSafety`; no overlap.
- Validation: passed. `NO_TASKS` and `NO_DELTAS` warnings are expected before prep/spec-delta work.
- Agenda: empty.
- Product-linked scope: none in `project.json`; current repo only.

### Current State

- Expensive semantic work is submitted through `asyncio.to_thread` in `src/lgrep/server/tools_semantic.py:125-147` and `src/lgrep/server/tools_semantic.py:287-290` without a visible job registry or cancellation state.
- Auto-indexing uses single-flight coordination in `src/lgrep/server/lifecycle.py:346-423`, but the actual `state.indexer.index_all` work runs in a default executor via `asyncio.to_thread` at `src/lgrep/server/lifecycle.py:378`.
- Global status gathers deep stats for every loaded project: `src/lgrep/server/tools_semantic.py:381-384` creates `_get_project_stats` tasks for every `app_ctx.projects` entry, and `_get_project_stats` calls `state.db.count_chunks` and `state.db.get_indexed_files` through `asyncio.to_thread` at `src/lgrep/server/lifecycle.py:314-315`.
- Watcher indexing/deletion also uses the default executor: `src/lgrep/watcher.py:97-115`.
- `StatusSemanticResult`/`StatusAllProjectsResult` currently expose files/chunks/watching/project/disk_cache/error only (`src/lgrep/server/responses.py:117-132`); no PID, uptime, active jobs, thread/executor stats, or timeout history.
- Timeout decorators exist in both `src/lgrep/server/__init__.py` and `src/lgrep/server/responses.py`, creating drift risk.
- README already recommends Vision tuning (`LGREP_WORKTREE_DEDUP=1`, explicit `LGREP_WARM_PATHS`, `LGREP_AUTO_WARM_DISK=false`, `LGREP_TOOL_TIMEOUT_S=8`) at `README.md:432-452`, but earlier text says shared HTTP is intended deployment mode at `README.md:400-414`.
- Tests cover single-project status shape and a search timeout returning an error, but not abandoned threaded work, global status cost, diagnostics, or conflict-marker CI guard.
- `CHANGELOG.md` contains unresolved merge markers at line 1 and line 6.

### Edge Cases

| Gap | Edge cases / failure modes |
|---|---|
| Job supervision | (1) `asyncio.wait_for` cancels the coroutine while the sync `to_thread` function continues; registry must not report it as active forever. (2) Two clients trigger same project auto-index; one disconnects while another awaits the single-flight event. (3) Shutdown occurs while index/search/status jobs are active. |
| Global status | (1) Dozens of stale alias paths exist in `app_ctx.projects`; no-arg status must stay cheap. (2) One project's LanceDB count call hangs/errors; global status must not fail all projects. (3) Existing clients expect `projects: [...]`; additive summary fields must not break typed output. |
| Diagnostics | (1) Diagnostics must expose enough path/job detail for local support but never env vars/API keys. (2) Completed/abandoned jobs need bounded history to avoid unbounded memory. (3) Diagnostic tool itself must not perform expensive disk work. |
| Cache/worktree safety | (1) Shared worktree cache must not delete chunks simply because one branch lacks a file. (2) Stale aliases should be surfaced without destructive cleanup. (3) Active-set skip behavior from `lgrepSemanticCacheLifecycle` must remain intact. |
| Conflict-marker guard | (1) Binary/generated files should not create false positives. (2) Markdown/docs should be scanned because `CHANGELOG.md` currently proves text-file risk. |

### Open Design Questions

| Question | Trust model | Blast radius | Alternatives / recommendation |
|---|---|---|---|
| How should sync work cancellation be represented? | Agent technical decision, with user outcome constraint | Wrong choice can corrupt cache, leak background CPU, or falsely report safety. | Prefer cooperative cancellation + abandoned/finished terminal states; do not kill unsafe sync operations. |
| Where should runtime supervision live? | Agent technical decision | Wrong locality can scatter lifecycle state and weaken shutdown cleanup. | Prefer `LgrepContext` owning a small runtime supervisor module used by semantic/status/watcher paths. |
| How should global status remain compatible while cheap? | Joint outcome decision | Existing agent/tool callers may rely on no-arg status shape. | Prefer additive cheap summary by default plus explicit deep/project status. Ask user below. |
| What diagnostics path/detail level is acceptable? | User outcome/privacy decision | Too little detail weakens support; too much may expose local paths. | Recommend local full paths + no secrets/env; ask user below. |
| Should shared HTTP/Vision defaults differ from stdio? | Joint product/default decision | Wrong default can surprise single-user or shared-daemon users. | Recommend shared-mode hardening defaults/docs; ask user preference on strictness. |
| Which spec owns daemon safety? | Agent technical decision | No spec means future regressions can pass proposal/design. | Create new `lgrepDaemonOperationalSafety` capability unless design finds existing spec better. |

### Draft Spec Deltas

New capability likely: `lgrepDaemonOperationalSafety`.

- `rq-daemon-jobs01` — Runtime tracks expensive jobs structurally.
  - Given an index/search/status/watch job starts, when diagnostics are requested, then job kind, project, started time/age, and state are reported without secrets.
- `rq-daemon-cancel01` — Timeout/cancellation leaves terminal state.
  - Given a tool invocation times out while sync work continues, when the wrapper returns, then the job is marked cancelled/abandoned or later finished, never permanently active.
- `rq-daemon-status01` — Global status is bounded by default.
  - Given many loaded projects, when `status_semantic(path="")` runs, then it avoids deep per-project LanceDB counts unless explicitly requested/scoped.
- `rq-daemon-diagnostics01` — Diagnostics are cheap and operator-safe.
  - Given a high-CPU support incident, when diagnostics are called, then PID/uptime/loaded project count/job summary/recent timeout state are returned without API keys or environment secrets.
- `rq-daemon-executor01` — Expensive sync work uses bounded execution.
  - Given concurrent tool calls submit blocking work, when they exceed configured concurrency, then work is queued/rejected with structured error rather than spawning unbounded default-executor activity.
- `rq-daemon-shared-mode01` — Shared deployment guidance is explicit.
  - Given README/install guidance for streamable HTTP/Vision, when a user follows it, then explicit warm paths, auto-warm behavior, timeouts, worktree dedup, and troubleshooting are documented.
- `rq-conflict-marker01` — Release hygiene rejects unresolved conflict markers.
  - Given tracked text files contain `<<<<<<<`, `=======`, or `>>>>>>>`, when tests/CI run, then the run fails with file/line evidence.

Potential deltas to existing `lgrepSemanticCacheLifecycle`:

- Add stale alias/cache visibility requirement only if design chooses to surface alias diagnostics through status rather than a new daemon diagnostics surface.

### Related Pattern Scan

Similar patterns found:

- Semantic tool thread work: `src/lgrep/server/tools_semantic.py:125`, `141`, `145`, `147`, `287`, `290`, `351`.
- Lifecycle/status thread work: `src/lgrep/server/lifecycle.py:188`, `314`, `315`, `378`, `381`.
- Watcher default executor work: `src/lgrep/watcher.py:106`, `114`.
- Symbol/maintenance tools also use `asyncio.to_thread` (`src/lgrep/server/tools_symbols.py`, `src/lgrep/server/tools_maintenance.py`); discovery keeps them in scan scope so a shared runtime helper can avoid semantic-only fixes if the same pattern matters.
- No existing active-job diagnostics found by text search for `active job`; only research-pack references exist.

### LBP Check

Likely direction matches LBP:

- FastMCP lifespan docs support typed application context and cleanup in `finally`; lgrep already uses `app_lifespan`, so adding runtime supervisor cleanup there is locally consistent.
- Python docs define `asyncio.to_thread` as a way to run blocking functions without blocking the event loop, but cancellation/timeout of the awaiting task is not a structural guarantee that the underlying sync function stops. Therefore explicit job state and bounded executor ownership are safer than assuming cancellation stops work.
- `concurrent.futures.Executor.shutdown(cancel_futures=True)` cancels pending futures, not necessarily already-running calls; design must distinguish queued vs running work.
- FastMCP structured output guidance aligns with adding typed diagnostic response models rather than prose/log-only diagnostics.
- External landscape from `docs/repo-improve-prep.md` shows local-first bounded-resource MCP daemons are a competitive pattern; daemon safety should precede graph/local-embedding product expansion.

### Recommended Objectives

1. Add structural daemon runtime supervision for expensive blocking work.
2. Make no-arg semantic status cheap and bounded by default while preserving scoped deep status.
3. Add operator-safe diagnostics for active jobs, recent timeouts, loaded projects, and process/runtime identity.
4. Preserve cache/worktree safety and existing prune transport guards.
5. Harden shared HTTP/Vision docs/defaults around warm paths, auto-warm, timeouts, dedup, and incident troubleshooting.
6. Add regression tests/CI guards for daemon timeout/cancellation/status behavior and conflict markers.

### AMBIGUITY ANALYSIS

| Finding | Severity | Category | Evidence | Reason |
|---|---|---|---|---|
| M1 | MEDIUM | Missing Information | `Acceptable default resource limits for typical WSL/Linux OpenCode hosts.` | Needs discovery/design decision; not blocking because safe bounded defaults can be selected and documented. |
| X1 | MEDIUM | External Dependency | `Whether LanceDB operations used here expose interruption/timeout controls.` | Requires API/version check in design; not blocking because wrapper can represent abandoned sync work even if underlying call is not cancellable. |

Coverage: B:C F:C S:C M:P

Trigger evaluation: no CRITICAL findings, no HIGH findings — proceed to agreement questions.