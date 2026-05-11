# Agreement

## Objectives
1. Fix all-projects `status_semantic` response — add `disk_cache`/`error` to `_get_project_stats()`.
2. Fix symbol incremental indexing — wire `detect_changes()` into `index_folder()` to remove deleted files/symbols.
3. Add `.adv/changes/` and `.adv/archive/` to default `.lgrepignore` template.
4. Add auto-staleness check to `search_semantic` — re-index if file hashes differ from current files.
5. Update agent guidance (SKILL.md, instruction, README) for stale-index recovery.
6. All tests pass.

## Acceptance Criteria
- **AC1:** `lgrep_status_semantic(path="")` returns valid response for all loaded projects — each nested entry includes `disk_cache: bool|None` and `error: str|None`, no validation errors.
- **AC2:** `index_symbols_folder(incremental=True)` removes symbols for files that no longer exist on disk. Regression test proves symbols for deleted file are absent after incremental re-index.
- **AC3:** Default `.lgrepignore` template excludes `.adv/changes/` and `.adv/archive/`. Existing `.lgrepignore` files are not overwritten. Test verifies template content.
- **AC4:** `search_semantic` checks staleness before returning results — if semantic index file hashes differ from current files, triggers re-index automatically and returns fresh results. Staleness check adds <100ms to warm-path searches.
- **AC5:** SKILL.md and instruction docs include stale-index recovery guidance. README troubleshooting updated.
- **AC6:** Full test suite passes (`pytest`).

## Constraints
- No breaking changes to existing tool signatures or response shapes.
- No new dependencies.
- Staleness check must be cheap (hash comparison, not full re-embed).
- Existing `.lgrepignore` files must never be overwritten silently.

## Avoidances
- Do not ignore `.adv/specs/` — specs are useful for agents.
- Do not replace LanceDB or symbol storage backend.
- Do not add background daemon/watcher auto-start changes.
- Do not add new env vars beyond existing `LGREP_*` pattern.

## Decisions
### User Decisions
- **UD1:** Ignore `.adv/changes/` + `.adv/archive/` only, keep `.adv/specs/` searchable.
- **UD2:** Auto-re-index if stale — `search_semantic` checks file hashes and re-indexes before returning results when mismatch detected.
- **UD3:** Wire `detect_changes()` into `index_folder()` — reuse existing tested method.

### Agent Decisions (LBP)
- **AD1:** Use hash-based staleness detection (SHA-256 file hashes) — industry standard, already in codebase.
- **AD2:** Staleness check runs in `_execute_search()` before embedding call — cheapest insertion point, reuses existing `Indexer.index_file()` hash-comparison logic.
- **AD3:** Fix `_get_project_stats()` to return complete dict with `disk_cache` and `error` fields — minimal change, consistent with single-project path.

## Deferred Questions
None.

## Sign-Off
User approved AC at 2026-05-11 via inline approval.