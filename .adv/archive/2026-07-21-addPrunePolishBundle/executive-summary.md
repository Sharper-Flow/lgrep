# Executive Summary

## Outcome
Three small follow-up items from `addPrunePolishBundle` review are now implemented: observability parity (structlog events in both prune paths), CLI ergonomics (`lgrep gc --symbols-dir DIR`), and CHANGELOG entry for the previously-shipped prune-symbols capability. No behavior change to existing surfaces.

## Why It Matters
Multi-agent hosts running the lgrep MCP daemon get structured-log visibility into security-relevant prune refusals (symlinks, out-of-root paths, unlink failures) without parsing JSON reports. Operators with relocated symbol stores can override via gc in one pass instead of invoking `prune-symbols` separately. CHANGELOG now documents the capability that shipped in `addPrunePolishBundle` (commit `6eafc1d3`).

## Verdict
APPROVED — 0 blockers, 0 issues. Inline review (small polish change; scanner fan-out not warranted for mirror-existing-pattern work).

## What Was Built
1. **Structlog parity** (`src/lgrep/tools/prune_orphans.py`, `src/lgrep/tools/prune_symbols.py`) — `log.warning(...)` events at all 3 failure-capture sites in both execute branches. Events: `prune_refused_symlink`, `prune_refused_outside_root`, `prune_unlink_failed`. Each carries a `store: "orphans"|"symbols"` kwarg for filterability. Pattern mirrors `index_store.py` and `tools/index_repo.py`. Existing `failures[]` capture unchanged (strictly additive per C2).
2. **gc --symbols-dir flag** (`src/lgrep/cli.py`) — new `--symbols-dir DIR` parsed identically to existing `--cache-dir`, forwarded via `prune_symbols(storage_dir=symbols_dir)`. Help text updated. Combined-report key set unchanged (DONT1).
3. **CHANGELOG entry** (`CHANGELOG.md`) — new `## Unreleased` section with 5 bullets covering: CLI subcommand, MCP tool, extended gc, LGREP_SYMBOLS_DIR env var, structlog observability. Closes issue #5.

## What Was Verified
- Verdict: APPROVED (inline review, 0 blockers / 0 issues).
- Tests: `uv run pytest tests/` → **657 passed, 0 failures** (was 648 baseline; +9 new: 6 capture_logs + 3 gc forwarding). `uv run ruff check .` → clean.
- Preview URL: `not_applicable` — pure CLI/library/code observability change, no browser/UI surface.
- Contract matrix: 15 rows persisted; 3 SC pass; 4 AC pass; 4 C respected; 5 DONT respected. 0 failing rows.

## Remaining Concerns
None. KD7 race mutex intentionally deferred (separate change if pursued).

## Supporting Evidence
- Code: `src/lgrep/tools/prune_orphans.py`, `src/lgrep/tools/prune_symbols.py`, `src/lgrep/cli.py`.
- Tests: `tests/test_prune_orphans.py` (+3 capture_logs), `tests/test_prune_symbols.py` (+3 capture_logs), `tests/test_cli.py::TestGcSymbolsDir` (+3 forwarding/help/absent).
- Docs: `CHANGELOG.md`.
- Contract matrix: 15 rows via `adv_contract_review_matrix_set`.
- Commit: `bdbd849` (single checkpoint covering all 3 tasks).

## Consequence Context
1. **Delivered value:** Observability parity + gc ergonomics + release-docs currency. No new surfaces; purely polish on existing capability.
2. **Enabling-only/follow-up dependency:** None.
3. **Ops readiness:** Additive only. Default dry-run unchanged; transport coercion unchanged; mutex unchanged.
4. **Migration/data impact:** `n/a` — no schema or behavior change.
5. **Frontend/preview impact:** `not_applicable`.
6. **Collision/release risk:** Very low. All changes additive; existing tests + combined-report shape preserved.
7. **Open follow-ups:** KD7 race mutex deferred (dedicated change if pursued).
8. **Next action:** Archive sign-off → `adv_change_archive phase9:"run"` → local deploy from merged main.

## Release Readiness Summary
- **Release gate ready:** All 7 gates complete (proposal, discovery, design, planning, execution, acceptance, release-pending). Contract matrix warning-free (0 failing rows).
- **Verification:** 657 tests pass, ruff clean.
- **Backlog items not blocking:** KD7 race mutex (separate change).
- **Archive readiness:** GREEN. Ready for Tier B sign-off.