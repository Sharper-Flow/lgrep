# Agreement

## Objectives

1. Add structural daemon runtime supervision for expensive blocking work.
2. Make no-arg semantic status cheap and bounded by default while preserving scoped deep status.
3. Add operator-safe diagnostics for active jobs, recent timeouts, loaded projects, full local project paths, and process/runtime identity.
4. Preserve cache/worktree safety and existing prune transport guards.
5. Harden shared HTTP/Vision docs/defaults around warm paths, auto-warm, timeouts, dedup, and incident troubleshooting.
6. Add regression tests/CI guards for daemon timeout/cancellation/status behavior and conflict markers.

## Acceptance Criteria

1. Timed-out/cancelled expensive daemon jobs are visible in job state as cancelled/abandoned/finished; never stuck “active” forever.
2. `lgrep_status_semantic(path="")` returns cheap bounded summary by default and avoids deep LanceDB per-project counts unless explicitly scoped/deep.
3. Diagnostics expose PID/uptime, loaded projects, active/recent jobs, job kind, full local project path, age, and terminal state; no secrets/env values.
4. Blocking sync work uses bounded runtime supervision, not unbounded default-executor fanout.
5. Shared cache/worktree safety remains intact; no silent chunk deletion/corruption and prune transport guards stay enforced.
6. Tests cover timeout/abandonment state, bounded global status, diagnostics shape, and conflict-marker rejection.
7. Docs explain Vision/shared HTTP safe settings and high-CPU/thread troubleshooting.
8. `CHANGELOG.md` conflict markers are removed.

## Constraints

- Preserve existing MCP tool names and response compatibility unless design explicitly justifies an additive or versioned change.
- Prefer structural correctness: job registry, typed response contracts, bounded executors, deterministic tests, and explicit runtime state over log scraping or heuristic inference.
- Keep shared-cache safety invariants from `lgrepSemanticCacheLifecycle`; do not delete or mutate live cache data implicitly.
- Do not expose secrets, environment values, or API keys in diagnostics.
- Do not weaken prune transport-safety guards.
- Avoid product novelty before daemon stability; graph/local-embedding ideas remain out of scope.

## Avoidances

- Do not make correctness depend on heuristic process-name/thread-count guessing alone.
- Do not let global status trigger unbounded per-project disk/LanceDB work by default.
- Do not silently discard or corrupt shared worktree cache data while improving cleanup/visibility.
- Do not restart or destructively clean the live Vision-managed daemon/cache as part of implementation without explicit operator action.
- Do not broaden into graph/code-impact features, local embedding backend migration, or public internet deployment platform work.

## Decisions

### User Decisions

- Timeout behavior: mark expensive daemon jobs as abandoned when timeout/cancellation occurs and observe/record eventual finish; no unsafe kill.
- Global status default: cheap bounded summary by default; deep per-project counts require scoped/explicit mode.
- Diagnostic path detail: full local paths are acceptable for local support diagnostics; secrets/env/API keys must not be exposed.

### Agent Decisions (LBP)

- Treat expensive sync calls as jobs owned by a structural runtime supervisor rather than relying on `asyncio.wait_for` to stop underlying thread work.
- Prefer additive diagnostics/status response fields or a typed diagnostic tool over log-only support guidance.
- Create or update daemon operational-safety spec requirements during design because no existing spec owns this runtime boundary.
- Preserve `lgrepSemanticCacheLifecycle` invariants and only add stale alias/cache visibility if needed.
- Keep conflict-marker detection as a CI/test hygiene guard for tracked text files.

## Deferred Questions

None.

## Sign-Off

User approved acceptance criteria via inline reply: `approve`.