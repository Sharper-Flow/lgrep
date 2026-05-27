# Archive: Fix daemon safety

**Change ID:** fixDaemonSafety
**Archived:** 2026-05-27T22:06:24.448Z
**Created:** 2026-05-27T20:41:52.803Z

## Tasks Completed

- ✅ Add lgrepDaemonOperationalSafety spec
  > Added `.adv/specs/lgrepDaemonOperationalSafety/spec.json` with requirements covering structured runtime jobs, timeout/cancellation terminal states, cheap global status, operator-safe diagnostics, bounded executor ownership, shared HTTP/Vision guidance, and conflict-marker guard behavior. Verified JSON syntax and that `.adv/specs/` remains searchable by default ignore template.
- ✅ Add RuntimeSupervisor and unit tests
  > Added `src/lgrep/server/runtime.py` with `RuntimeSupervisor`, `JobStatus`, bounded `ThreadPoolExecutor`, active/recent job snapshots, cancellation/abandonment handling, and shutdown cleanup. Integrated `RuntimeSupervisor` into `LgrepContext` and shutdown. Added `tests/test_daemon_runtime.py` covering timeout abandonment then terminal state, worker-limit/history bounding, and sync exception terminal summaries.
- ✅ Integrate supervisor into semantic lifecycle and bounded global status
  > Added _run_blocking helper for semantic tools and routed staleness checks, DB search, explicit indexing, latest-index timestamp reads, scoped status DB counts, and disk-cache status reads through LgrepContext.runtime. Changed no-arg lgrep_status_semantic to return memory-only summary entries with additive summary_only/detail fields instead of global LanceDB count fanout. Updated lifecycle single-flight auto-index and scoped project stats to use RuntimeSupervisor. Added tests proving global status avoids DB calls, scoped status/index/search use runtime supervision, and error/compatibility fields remain present.
- ✅ Add lgrep_diagnostics tool and typed diagnostics tests
  > Added src/lgrep/server/tools_diagnostics.py with lgrep_diagnostics using LgrepContext.runtime snapshots and loaded project state without expensive disk work. Added DiagnosticsResult/LoadedProjectEntry/TimeoutAbandonmentSummary TypedDicts, registered/exported diagnostics tool, updated server tool count, and added diagnostics tests covering shape, loaded projects, job snapshots, abandonment summary, and secret/env exclusion.
- ✅ Route watcher and maintenance blocking work through supervisor
  > Passed LgrepContext.runtime into FileWatcher from watch_start_semantic and routed watcher incremental index/delete work through RuntimeSupervisor when available. Routed MCP prune_orphans, invalidate_worktree_cache, and startup orphan sweep through RuntimeSupervisor while keeping ctx=None test fallback and HTTP destructive prune dry-run coercion intact. Added watcher and maintenance tests proving runtime routing and preserved cache/prune safety behavior.
- ✅ Add conflict-marker guard and clean CHANGELOG.md
  > Resolved nested conflict markers in `CHANGELOG.md` and added `tests/test_conflict_markers.py`, a tracked-file guard using `git ls-files` that reports file:line evidence for unresolved Git conflict markers while skipping binary/cache/generated paths. Orchestrator adjusted the delegated test to include line numbers and pass Ruff import/style checks.
- ✅ Update shared HTTP/Vision docs and installer guidance
  > Updated README and lgrep skill guidance to position stdio as local default and shared HTTP as intentional daemon mode; documented LGREP_AUTO_WARM_DISK, LGREP_WORKER_MAX_THREADS, bounded warm paths, diagnostics, cheap no-arg status, timeout fallback, and shared-HTTP prune dry-run behavior. Updated install_opencode systemd/manual guidance with safe shared daemon defaults and troubleshooting steps. Added tests asserting README and installer include the daemon safety controls.
- ✅ Run full contract verification sweep
  > Executed full test and lint verification. Initial full pytest exposed stale registration expectations (18 tools and missing lgrep_diagnostics); updated tests/test_server_registration.py to expect 19 tools and include lgrep_diagnostics. Reran registration tests, full pytest, full ruff, and daemon spec JSON validation successfully.

## Specs Modified


## Wisdom Accumulated

- **[gotcha]** For Python 3.13/Ruff TCH rules with `from __future__ import annotations`, `collections.abc.Callable` used only in annotations must be imported inside `if TYPE_CHECKING:`. Ruff may report both UP035 and TC003 in sequence.
- **[pattern]** When adding an MCP tool in this repo, update both the server import/export surface and the explicit server tool registration/count test; otherwise focused tool-list tests fail even if the tool implementation is correct.
- **[pattern]** For status APIs that must become cheap by default while preserving compatibility, keep legacy required fields and add explicit optional fields like `summary_only`/`detail` so callers can distinguish omitted expensive counts from real zero counts.
