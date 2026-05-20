# Agreement: Worktree-Aware Cache Lifecycle

## Objectives

1. **Cache-key deduplication** — When `LGREP_WORKTREE_DEDUP=1` is set, `get_project_db_path()` resolves the git common-dir and uses it as the cache key instead of the raw filesystem path. Two worktrees sharing a `.git` common-dir produce one LanceDB cache directory.

2. **Archive-hook invalidation surface** — New MCP tool `invalidate_worktree_cache(paths: list[str])` that removes only the worktree-specific cache entries (meta, not the canonical LanceDB data) so ADV can call it during `/adv-archive` Phase 9. Returns structured `WorktreeInvalidationResult` with paths cleaned, bytes reclaimed, errors.

3. **Automatic orphan sweep** — On server start, schedule a one-shot `prune_orphans(dry_run=False)` after a 5-minute delay. Reuses existing grace window (1h default) and all TOCTOU guards. Logged at info level.

4. **`lgrep gc` CLI subcommand** — Wraps `prune_orphans --execute` and worktree-cache cleanup. Suitable for cron/systemd timer. Follows existing CLI subcommand pattern (see `_cmd_prune_orphans`).

5. **Zero regression for single-project usage** — When `LGREP_WORKTREE_DEDUP` is unset (default), `get_project_db_path()` behaves identically to current main. No changes to cache layout, search latency, or token cost.

## Acceptance Criteria

1. **Dedup correctness** — Indexing the same repo at two worktree paths with `LGREP_WORKTREE_DEDUP=1` produces exactly one LanceDB cache directory. `lgrep_search_semantic` from either path returns identical results.

2. **Zero re-embed verification** — When dedup is enabled and the canonical cache already exists, `get_project_db_path(worktree_B)` returns the same hash dir as `get_project_db_path(canonical_trunk)`. The indexer's existing staleness check skips re-embedding because chunks are already present. Verified by: (a) asserting cache-path equality in tests, (b) asserting `Indexer.index_all()` reports 0 new embeddings on the second worktree.

3. **MCP invalidation tool** — `invalidate_worktree_cache(paths: list[str])` returns `WorktreeInvalidationResult(paths_cleaned: int, bytes_reclaimed: int, errors: list)`. Refuses paths whose resolved cache dir is outside `LGREP_CACHE_DIR`. Does NOT delete the canonical LanceDB data — only removes `project_meta.json` and unlinks the worktree from in-memory project map.

4. **Post-archive cleanliness** — After calling `invalidate_worktree_cache` for a worktree path, the worktree's `project_meta.json` is removed and the path is removed from `LgrepContext.projects`. The canonical cache directory and its `project_meta.json` remain intact.

5. **Startup sweep** — Server startup schedules `prune_orphans(dry_run=False)` after 300s delay. Sweep respects existing grace window (1h default). Active in-memory projects are passed as `active_set` and skipped. Logged via structlog at info level.

6. **`lgrep gc` CLI** — New subcommand `lgrep gc [--execute] [--dry-run] [--cache-dir DIR]` wrapping both `prune_orphans` and worktree-meta cleanup. Help text and flag parsing follow existing `_cmd_prune_orphans` pattern.

7. **Existing tests pass** — `test_prune_orphans.py`, `test_server.py`, `test_symbol_tools.py`, `test_e2e_symbols.py`, `test_storage.py`, `test_cli.py` all pass unchanged.

8. **New tests** — Cover: (a) canonical-key resolution for git worktrees vs fallback for non-git paths, (b) dedup on/off flag behavior, (c) `invalidate_worktree_cache` security guards (path confinement, refuse outside cache root), (d) background sweep with active projects skipped, (e) `lgrep gc` CLI flag parsing.

9. **Documentation** — `README.md` section on worktree workflow + opt-in env var. `docs/` integration note for ADV users showing archive-hook call pattern. `lgrep gc --help` output.

10. **No regression** — Single-repo non-worktree usage: identical cache layout (same hash for same resolved path), identical token cost, identical search latency vs current main. Verified by running existing test suite + manual comparison.

## Constraints

- **Opt-in only** — `LGREP_WORKTREE_DEDUP` defaults to off. No behavior change without explicit opt-in.
- **No symlink hacks** — Per architecture discipline policy. Dedup is via cache-key remapping, not filesystem symlinks.
- **No new config system** — Use env var only, consistent with lgrep's existing configuration pattern.
- **No in-memory project dedup in v1** — `LgrepContext.projects` stays keyed by resolved path. Disk-level dedup is the first slice; in-memory dedup (one ProjectState per common-dir, multiple path aliases) is deferred to a follow-up.
- **Grace window reuse** — Startup sweep reuses the existing `LGREP_PRUNE_MIN_AGE_S` mechanism. No new timing constants.
- **Path-confinement everywhere** — All new destructive operations (invalidation, GC) enforce resolved-path under `LGREP_CACHE_DIR`, matching the pattern in `prune_orphans`.

## Avoidances

- Replacing LanceDB or the Voyage Code 3 embedder
- Redesigning the symbol-index store (`IndexStore` keying stays per-path)
- Network/auth changes to MCP transport
- Indexing changes that affect ranking quality
- File-watcher-driven cross-worktree live sync
- Distributed cache across machines
- Shipping systemd timer units (document only; installer stays small)
