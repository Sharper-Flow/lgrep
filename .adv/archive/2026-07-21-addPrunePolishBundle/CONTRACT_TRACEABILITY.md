# Contract Traceability

**Change ID:** addPrunePolishBundle
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-21T04:50:00.000Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| SC1 | success_criterion | pass | review | Both prune_orphans.py and prune_symbols.py emit log.warning at all 3 failure-capture sites. 6 capture_logs tests pass (3 per file). Events: prune_refused_symlink, prune_refused_outside_root, prune_unlink_failed. store kwarg distinguishes orphans/symbols. |
| SC2 | success_criterion | pass | review | _cmd_gc --symbols-dir DIR parses and forwards via prune_symbols(storage_dir=symbols_dir). test_gc_symbols_dir_forwarded_to_prune_symbols verifies Path resolution. |
| SC3 | success_criterion | pass | review | CHANGELOG.md has '## Unreleased' entry describing CLI subcommand, MCP tool, extended gc, LGREP_SYMBOLS_DIR, structlog observability. Format matches existing v3.1.7/3.1.8. |
| AC1 | acceptance_criterion | pass | test | prune_orphans.py:333,347,364,389 + prune_symbols.py:378,392,409,435 — log.warning at all 3 failure sites in both files. tests/test_prune_orphans.py + tests/test_prune_symbols.py: 6 new capture_logs tests pass. |
| AC2 | acceptance_criterion | pass | test | cli.py:419,439,451-453,465 — help text, parser, forwarding. tests/test_cli.py::TestGcSymbolsDir: 3 tests (forwarding, absent-passes-None, help-text). |
| AC3 | acceptance_criterion | pass | test | CHANGELOG.md:1-16 — new '## Unreleased' entry with all 5 bullet points (CLI subcommand, MCP tool, gc extension, env var, structlog). Cross-referenced against shipped code 6eafc1d3. |
| AC4 | acceptance_criterion | pass | test | Full suite 657 passed (was 648; +9 new: 6 capture_logs + 3 gc forwarding). Ruff clean. TDD: structlog tests written first then implementation added (existing tests still green during transition). |
| C1 | constraint | respected | static_check | Event names identical between files: prune_refused_symlink, prune_refused_outside_root, prune_unlink_failed. Kwargs shape identical (path, store, [error]). Only store value differs ('orphans' vs 'symbols'). |
| C2 | constraint | respected | static_check | git diff shows log.warning calls are ADDED alongside existing failures.append calls; no failures.append payload modified. Existing failure tests still pass unchanged. |
| C3 | constraint | respected | static_check | import structlog + log = structlog.get_logger() pattern matches index_store.py:23-25 and tools/index_repo.py:15-18. Event names follow snake_case convention with descriptive kwargs. |
| C4 | constraint | respected | static_check | git diff main..HEAD -- src/lgrep/ shows no new os.environ references. No new env knobs. |
| DONT1 | avoidance | respected | review | cli.py:466-470 combined dict still has exactly {prune_orphans, gc_worktree_meta, prune_symbols} keys. test_gc_combined_report_includes_prune_symbols still passes. |
| DONT2 | avoidance | respected | review | cli.py --cache-dir help text and parser unchanged. Only --symbols-dir added. |
| DONT3 | avoidance | respected | review | No fcntl/flock/lock-file imports added. KD7 deferred. |
| DONT4 | avoidance | respected | review | git diff shows only log.warning calls added to prune_orphans.py and prune_symbols.py. No classification, guard, or return-shape changes. |
| DONT5 | avoidance | respected | review | _cmd_prune_symbols standalone CLI surface unchanged (still --execute/--dry-run/--storage-dir). Only _cmd_gc got the new --symbols-dir flag. |
| OOS1 | out_of_scope | missing | not_applicable |  |
| OOS2 | out_of_scope | missing | not_applicable |  |
| OOS3 | out_of_scope | missing | not_applicable |  |
| OOS4 | out_of_scope | missing | not_applicable |  |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-f4d35ef699fb | AC1, SC1 | AC4 | C1, C2, C3 |  |
| tk-e7758df671b2 | AC2, SC2 |  | DONT1, DONT2 |  |
| tk-35ba33994af2 | AC3, SC3 |  | DONT4 |  |
