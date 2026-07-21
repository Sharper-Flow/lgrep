# Contract Traceability

**Change ID:** addSymbolStorePrune
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-21T04:15:00.000Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| C1 | constraint | respected | static_check | tools/prune_symbols.py:108-118 (_is_under confinement), 239-251 (scan-side symlink refusal), 267 (grace window via _GRACE_EXEMPT_REASONS), 372-414 (delete-time TOCTOU + path-confinement + per-entry failure isolation); cli.py:374-379 + 428-433 (--execute/--dry-run mutex returns 2). 17 parity assertions across tests/test_prune_symbols.py. |
| C2 | constraint | pass | static_check | tools/prune_symbols.py:318 (dry_run: bool = True default); cli.py:381 (dry_run = True default in _cmd_prune_symbols); server/tools_maintenance.py:153 (default True in MCP handler). Test test_prune_default_dry_run_is_true pins default. |
| C3 | constraint | pass | static_check | server/tools_maintenance.py:57-81 (_transport_is_local helper reused from prune_orphans) + 169-172 (effective_dry_run = True when transport non-local). Test test_mcp_prune_symbols_non_stdio_coerces_dry_run_true passes caller dry_run=False and asserts effective dry_run=True. |
| C4 | constraint | pass | static_check | tools/prune_symbols.py:64 (_NONLOCAL_PREFIX='github:') + 183-184 (skip in _classify before any is_dir check). Test test_find_stale_skips_github_entries asserts results == [] for github:owner/name@ref. |
| C5 | constraint | pass | static_check | git diff main...HEAD -- src/lgrep/tools/invalidate_cache.py src/lgrep/server/tools_symbols.py returns empty. invalidate_cache handler untouched. |
| C6 | constraint | pass | static_check | git diff main...HEAD -- src/lgrep/storage/index_store.py returns empty. IndexStore.save still writes {repo_path, files, symbols, version} via temp+rename; default version=2.0 unchanged. |
| C7 | constraint | pass | static_check | tools/prune_symbols.py:154-198 (_classify) bases staleness on json.loads + Path(repo_path).is_dir() existence checks, NOT on mtime/age. Grace window at line 267 only suppresses ambiguous unreadable_index_json reason, never the sole authority for stale classification. |
| C8 | constraint | pass | static_check | tools/prune_symbols.py:86-94 (_grace_seconds reads LGREP_PRUNE_MIN_AGE_S, falls back to _DEFAULT_GRACE_SECONDS=3600 on missing/invalid). No new env knob introduced; LGREP_SYMBOLS_DIR exists only for storage-dir override (per agreement discovery resolution #5, distinct from grace). |
| DONT1 | avoidance | respected | review | git diff main...HEAD -- src/lgrep/tools/prune_orphans.py returns empty. tests/test_prune_orphans.py unchanged; 17/17 still pass. CLI flag shape, output shape, and handler behavior identical. |
| DONT2 | avoidance | respected | review | cli.py:459-466 combined report has 3 keys {prune_orphans, gc_worktree_meta, prune_symbols}. Test test_gc_combined_report_includes_prune_symbols asserts exact key set; test_gc_preserves_existing_key_behavior verifies dry_run threading for all three. |
| DONT3 | avoidance | respected | review | tools/prune_symbols.py:380-388 (symlink refusal appends to failures[]), 397-398 (resolve OSError captured), 413-414 (unlink OSError captured). All branches `continue` rather than raise. Test test_prune_execute_continues_on_unlink_failure monkeypatches Path.unlink to raise and asserts second stale entry still deleted. |
| DONT4 | avoidance | respected | review | server/tools_maintenance.py:169-172 coerces effective_dry_run=True when _transport_is_local(ctx) is False. Test test_mcp_prune_symbols_non_stdio_coerces_dry_run_true passes dry_run=False via streamable-http transport and asserts True. |
| DONT5 | avoidance | respected | review | Same evidence as C6: git diff src/lgrep/storage/index_store.py empty. File format and version field unchanged. |
| OOS1 | out_of_scope | missing | not_applicable |  |
| OOS2 | out_of_scope | missing | not_applicable |  |
| OOS3 | out_of_scope | missing | not_applicable |  |
| OOS4 | out_of_scope | missing | not_applicable |  |
| OOS5 | out_of_scope | missing | not_applicable |  |
| OOS6 | out_of_scope | missing | not_applicable |  |
| OOS7 | out_of_scope | missing | not_applicable |  |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-3bac9fdf44bd | C1, C7 |  | C4, C8, DONT3 |  |
| tk-9e4df88dd659 | C3 |  | DONT4 |  |
| tk-1e3498516892 | C2 |  | DONT1, DONT2 |  |
| tk-f0b7e83176a2 | C1 |  | C4, C8 |  |
| tk-4732fe760fab |  |  | DONT1, DONT2 |  |
