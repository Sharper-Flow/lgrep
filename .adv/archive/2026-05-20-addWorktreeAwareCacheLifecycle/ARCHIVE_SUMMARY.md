# Archive: Add worktree-aware cache lifecycle

**Change ID:** addWorktreeAwareCacheLifecycle
**Archived:** 2026-05-20T07:04:31.811Z
**Created:** 2026-05-20T05:49:22.927Z

## Tasks Completed

- ✅ ## Add `canonical_repo_key()` and refactor `get_project_db_path()`
  > Added canonical_repo_key() with git rev-parse --path-format=absolute --git-common-dir. Refactored get_project_db_path() to use canonical key. 10 new tests, 85 existing tests pass.
- ✅ ## Guard stale-file deletion when worktree dedup is enabled
  > Added _dedup_enabled flag to Indexer.__init__. Guarded stale-file deletion in index_all() with if not self._dedup_enabled. 2 new tests pass. Prevents cross-worktree chunk corruption in shared LanceDB.
- ✅ ## Add `alias_paths` support to `project_meta.json`
  > Extended write_project_meta with alias_paths parameter. Implements read-modify-write merge with deduplication. Documented concurrent-write race in docstring. 3 new tests pass.
- ✅ ## Add `invalidate_worktree_cache` MCP tool
  > Implemented by adv-engineer: WorktreeInvalidationEntry/Result TypedDicts, invalidate_worktree.py with path confinement + symlink guards, MCP tool in tools_maintenance.py with in-memory project eviction, wired in __init__.py. 5 new tests pass, 124 total.
- ✅ ## Add background orphan sweep on server start
  > Added _schedule_startup_sweep() to lifecycle.py: 5-min delayed one-shot prune_orphans(dry_run=False). Scheduled via asyncio.create_task in app_lifespan, cancelled on shutdown. 2 new tests pass.
- ✅ ## Add `lgrep gc` CLI subcommand
  > Added _cmd_gc to cli.py wrapping prune_orphans. Flag parsing (--execute, --dry-run, --cache-dir) matches _cmd_prune_orphans pattern. Help text updated. 5 new tests pass.
- ✅ ## Cross-cutting integration tests
  > E2E dedup test (two git worktrees → one cache dir). Campsite fixes for tool registration (17→18). 477 passed, 0 failures.
- ✅ ## Documentation: README, ADV integration, CLI help
  > Added LGREP_WORKTREE_DEDUP to env var table. Added 'Git worktree workflow' section to README with dedup explanation, stale-file tradeoff, ADV integration pattern, and gc usage.
- ✅ ## In-memory ProjectState dedup via canonical-key sharing
  > In-memory dedup via _canonical_to_state on LgrepContext. _ensure_project_initialized aliases shared state when canonical key already exists. remove_project preserves state if other refs remain. _shutdown stops each watcher exactly once via id() dedup. MAX_PROJECTS counts canonical projects, not aliases. 3 new tests + 1 campsite fix. 480 passed.
- ✅ ## Add `gc_worktree_meta` helper + integrate into `lgrep gc`
  > Added gc_worktree_meta to prune_orphans.py: scans all project_meta.json files, removes alias entries whose dirs no longer exist. Atomic rewrite via tmp+rename. _cmd_gc now runs BOTH prune_orphans AND gc_worktree_meta. 5 new tests, 485 passed.
- ✅ ## fcntl.flock guard around `write_project_meta` read-modify-write
  > fcntl.flock guard around write_project_meta read-modify-write. Lock on dedicated .meta.lock file (not meta itself, since atomic rename would invalidate). Graceful fallback to unguarded write on non-POSIX (Windows) with one-time warning. Verified with 4-process × 20-write concurrency test. 488 passed.

## Specs Modified


## Wisdom Accumulated

- **[gotcha]** asyncio.create_task + cancellation pattern: when the task catches CancelledError internally and returns gracefully, the awaiting code does NOT see CancelledError — it just gets the normal return value. Test the behavioral effect (sweep didn't run) rather than the exception type.
- **[pattern]** When dedup'ing in-memory dict values across multiple keys (one ProjectState shared by many path keys), use `id(obj)` set to deduplicate iterations like teardown loops. Avoids double-stopping the same watcher.
- **[pattern]** fcntl.flock on a file you intend to atomically replace (via tmp+rename) is broken: rename swaps the inode out from under the lock, so a second locker holds a lock on the OLD file. Solution: lock on a separate dedicated lock file (e.g. `.meta.lock`) that NEVER gets renamed, while the protected payload file gets the atomic write.
