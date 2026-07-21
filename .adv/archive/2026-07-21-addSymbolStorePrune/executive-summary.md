# Executive Summary

## Outcome
Operators can now reclaim disk held by stale symbol indexes (`~/.cache/lgrep/symbols/index_*.json`) that point at deleted worktree paths, via a new `lgrep prune-symbols` CLI subcommand, an extended `lgrep gc` umbrella, or a new `prune_symbols` MCP tool. The reviewer recommends approval; the work ships the requested capability without changing any existing surface.

## Why It Matters
GitHub issue #5 reported that the symbol store accumulates indexes forever on multi-agent hosts where per-change worktrees are created and deleted by ADV workflows; roughly half of the on-disk indexes can point at deleted paths. There was no CLI or MCP surface to find or prune them, and the documented `lgrep gc` mental model ("garbage collection") covered only the semantic cache. This change closes that gap: a single command now reclaims the disk and shrinks `lgrep list_repos` output back to live repos.

## Verdict
APPROVED — 0 blockers, 0 issues, 4 suggestions deferred to /adv-harden (edge-case test fixtures, default-storage-dir import dedupe, CLI dispatch parity test, one spec-delta wording smell), 3 nits deferred. Scanner-raised "missing spec artifact" blocker rejected_with_evidence: the ADV delta model persists deltas on the change record via `adv_delta_add` and applies them to global spec files at archive time; the 6 deltas for capability `lgrep-symbol-store-lifecycle` are validated and well-formed.

## What Was Built
1. **Symbol-store pruner core** (`src/lgrep/tools/prune_symbols.py`, ~340 lines) — `find_stale_indexes()` + `prune_symbols()` with 3-reason classification (`repo_path_enoent`, `unreadable_index_json`, `missing_repo_path_field`), `github:` skip, grace window reusing `LGREP_PRUNE_MIN_AGE_S` (only `unreadable_index_json` grace-eligible), symlink refusal at scan and delete, path-confinement at delete, per-entry failure isolation in `failures[]`, `dry_run=True` default, per-file `stat().st_size` byte accounting.
2. **MCP tool** (`src/lgrep/server/tools_maintenance.py` + `responses.py` + `__init__.py`) — `prune_symbols` MCP tool registered with `_transport_is_local()` transport-safety coercion; `PruneSymbolsResult` and supporting TypedDicts exported.
3. **CLI surface** (`src/lgrep/cli.py`) — `_cmd_prune_symbols` dispatcher mirroring `_cmd_prune_orphans` flag shape (`--execute`/`--dry-run`/`--storage-dir`, mutex exit 2); `_cmd_gc` extended to invoke the new sweep and nest its report under the `prune_symbols` key (existing keys preserved); gc help text updated to reflect 3 sweeps.
4. **Spec capability** — 6 requirements written to capability `lgrep-symbol-store-lifecycle` via `adv_delta_add` (classification, skip-nonlocal, dry-run-default, guards, mcp-safety, gc-umbrella). Persisted on the change record; will be applied to global spec files at archive time.
5. **Tests** — 19 new tests in `tests/test_prune_symbols.py` (AC1, AC2, AC5, AC6, AC7, AC9, AC10 + classification + active_set + files_examined); 4 new MCP tests in `tests/test_server_tools.py` (AC8 transport coercion regression + response shape + stdio honoring); new CLI tests in `tests/test_cli.py` (AC3 mutex exit 2 + AC4 gc nesting with existing-key preservation); `tests/test_server_registration.py` updated 19→20 tool count.
6. **Docs** — README.md updated with new sections (Optional: inspect or prune stale symbol-store indexes; Troubleshooting; prune_symbols MCP table entry; LGREP_SYMBOLS_DIR env var; 3-pass gc; transport-safety bullet).

KD7 honored: design explicitly chose NOT to add a writer/pruner mutex to prune_symbols alone; the same scan→unlink race exists today in `prune_orphans` and the proper fix covers both paths in one future change. Strict C1 parity preserved.

## What Was Verified
- Verdict: APPROVED with 0 blockers / 0 issues / 4 suggestions (deferred) / 3 nits / 8 info findings (4 rejected_with_evidence as parity-or-out-of-scope).
- Tests: `uv run pytest tests/` → **641 passed, 0 failures** (was 611 baseline; +30 new across the three new test surfaces). `uv run ruff check .` → clean.
- Preview URL: not_applicable — pure CLI/library/MCP change with no browser/UI surface; visual_surface: false per agreement.
- Contract matrix: 20 rows persisted; 8 constraints + 5 avoidances pass/respected; 7 out-of-scope items not_applicable. 0 failing rows.
- TDD: red→green documented per code task (ModuleNotFoundError / Missing tool / ImportError); assertion depth verified by scanner (tautology check clean; no skipped tests).

## Remaining Concerns
- **Deferred to /adv-harden (non-blocking):** edge-case test fixtures for `_classify` JSON-shape tolerance (non-object JSON, zero-byte file, `repo_path:null`, non-string `repo_path`); import `_DEFAULT_SYMBOLS_DIR` from IndexStore to prevent drift; add `TestCLIDispatch.test_main_dispatches_to_prune_symbols` parity test; rewrite `rq-2bF6tR8nKp` to remove `SMELL_TOTALITY` validator smell.
- **Backlog (separate changes):** structlog per-event logging for refused symlink/out-of-root across BOTH prune paths (parity fix); `gc --symbols-dir` passthrough (ergonomics); CHANGELOG entry (release-prep convention).
- **Known race (accepted per KD7):** scan→unlink race against a concurrent `IndexStore.save` writer. Inherited from `prune_orphans` (same pattern at `prune_orphans.py:323-365`). Adding guards only to `prune_symbols` would diverge from C1; the proper fix covers both paths in one future change.

## Supporting Evidence
- Code: `src/lgrep/tools/prune_symbols.py`, `src/lgrep/server/tools_maintenance.py` (prune_symbols handler), `src/lgrep/cli.py` (`_cmd_prune_symbols` + extended `_cmd_gc`), `src/lgrep/server/responses.py` (`PruneSymbolsResult` TypedDicts).
- Tests: `tests/test_prune_symbols.py` (19), `tests/test_server_tools.py::TestPruneSymbolsTool` (4), `tests/test_cli.py::TestCmdPruneSymbols` + `TestGcPruneSymbols`, `tests/test_server_registration.py` (tool count updated).
- Scanner bundle: `adv-scanner-bundle` report at attempt 1, 12 dimensions covered by 3 explore scanners.
- Contract matrix: 20 rows persisted via `adv_contract_review_matrix_set`.
- Task IDs: tk-3bac9fdf44bd (core), tk-9e4df88dd659 (MCP), tk-1e3498516892 (CLI), tk-f0b7e83176a2 (spec deltas), tk-4732fe760fab (docs).
- Commits: 45a3074 (core checkpoint), c994b6f (MCP+CLI), ac148db (README), 2a9e44a (help text).

## Consequence Context
1. **Delivered value:** New `lgrep prune-symbols` CLI + MCP surface reclaims disk from stale symbol indexes; extends `lgrep gc` to cover both on-disk stores in one pass.
2. **Enabling-only/follow-up dependency:** None blocking. Deferred harden items (test coverage polish, import dedupe, spec wording) are non-blocking; backlog items (structlog, gc --symbols-dir, CHANGELOG) are separate changes.
3. **Ops readiness:** Harden owns release/deploy/production/docs/cleanup readiness. Default dry-run on every surface; --execute/--dry-run mutex exits non-zero on conflict; transport coercion enforces dry-run on shared HTTP transports.
4. **Migration/data impact:** n/a — no schema migration, no data transformation. Existing symbol indexes are read-only consumers; new sweep is additive.
5. **Frontend/preview impact:** not_applicable — no browser/UI surface.
6. **Collision/release risk:** Low. All surfaces are additive. Existing `prune-orphans`, `gc` keys, and `invalidate_cache` semantics unchanged. `tests/test_server_registration.py` updated to reflect 20-tool count (necessary sibling-test fix).
7. **Open follow-ups:** 4 harden-track suggestions + 3 backlog items + 1 release-prep CHANGELOG note. None blocking release.
8. **Next action:** Acceptance approval proceeds inline to `/adv-harden addSymbolStorePrune`; fixes/re-entry/split/stop follow the standard reply parser.