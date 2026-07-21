# Archive Briefing Digest

**Change ID:** addSymbolStorePrune
**Title:** Add symbol store prune
**Status:** archived
**Generated:** 2026-07-21T04:23:21.323Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY
- Origin: triage #5

## Archive Digest

**Status:** archived

| Gate | Status |
| --- | --- |
| proposal | done |
| discovery | done |
| design | done |
| planning | done |
| execution | done |
| acceptance | done |
| release | pending |

## Epic Context

No Epic membership

## Durable Facts

Showing 48 of 48 durable facts.

- **[unresolved_action]** required_main_agent_actions: Run the requested `adv_task_checkpoint taskId:tk-9e4df88dd659 mode:complete` from the orchestrator side; it is not available to this leaf agent.
- **[archive_only_evidence]** decisions: Updated tests/test_server_registration.py to expect 20 tools and include prune_symbols — The existing registration test hard-coded the 19-tool count; registering a new MCP tool necessarily broke it, and the full-suite 0-failures acceptance criterion required updating it.
- **[archive_only_evidence]** decisions: Added prune_symbols to src/lgrep/server/__init__.py imports and __all__ — Keeps public API parity with prune_orphans and makes `from lgrep.server import prune_symbols` work.
- **[archive_only_evidence]** decisions: Used a fake RuntimeStub in transport-safety tests — Passing a context triggers the runtime.run_blocking path in _run_blocking; a stub lets the handler execute without a real supervisor.
- **[unresolved_action]** scope_drift: finish_owned_scope_then_report: tests/test_server_registration.py was not in the original allowed file list, but updating it was unavoidable for the full-suite 0-failures requirement. It is a direct sibling test of the registration behavior being changed.
- **[archive_only_evidence]** verification: uv run pytest tests/test_server_tools.py -x --tb=short --no-header (1) — Red phase: TestToolRegistration.test_all_19_tools_registered failed with Missing: {'prune_symbols'} before handler was implemented
- **[archive_only_evidence]** verification: uv run pytest tests/ --tb=short --no-header -q (0) — Green phase: 641 passed, 0 failures
- **[archive_only_evidence]** verification: uv run ruff check src/lgrep/server/ tests/test_server_tools.py tests/test_server_registration.py (0) — All checks passed
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: manual-red-phase
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: manual-lint
- **[archive_only_evidence]** decisions: Mirrored _cmd_prune_orphans flag parsing for _cmd_prune_symbols with --execute/--dry-run/--storage-dir — Keeps CLI surface consistent and reuses the existing mutually-exclusive check pattern (if both flags present, print to stderr and return 2)
- **[archive_only_evidence]** decisions: Inserted prune_symbols report into _cmd_gc after the existing prune_orphans and gc_worktree_meta keys — Preserves existing-key behavior exactly while adding the new prune_symbols key as required by AC4
- **[archive_only_evidence]** verification: uv run pytest tests/test_cli.py -x --tb=short --no-header (0) — 48 tests passed (targeted CLI suite)
- **[archive_only_evidence]** verification: uv run ruff check src/lgrep/cli.py tests/test_cli.py (0) — All checks passed
- **[archive_only_evidence]** verification: uv run pytest tests/ --tb=short --no-header -q (0) — 641 passed, 0 failures, 16 warnings
- **[archive_only_evidence]** decisions: Added a new README section 6 for `lgrep prune-symbols` mirroring the existing `prune-orphans` section, including parallel troubleshooting subsection. — Task requested parallel documentation with same tone/structure.
- **[archive_only_evidence]** decisions: Added `LGREP_SYMBOLS_DIR` to the environment variables table and extended the shared-HTTP destructive-cleanup bullet to include `lgrep prune-symbols --execute`. — The storage dir is a real operator knob (prune_symbols.py:121-128) and the shared-HTTP safety applies to the new MCP tool too.
- **[archive_only_evidence]** decisions: Did not edit `src/lgrep/cli.py` help text for `lgrep gc`, even though it does not mention the symbol-store sweep. — The task's explicit constraint prohibits touching Python source files in `src/lgrep/`; the sweep is documented in README instead.
- **[archive_only_evidence]** verification: uv run ruff check . (0) — All checks passed
- **[archive_only_evidence]** verification: uv run pytest tests/ --tb=short --no-header -q (0) — 641 passed, 0 failures, 16 warnings
- **[report_follow_up]** follow_ups: Packet did not include verbatim AC1-AC11 or proposed new capability text; validate them when created.
- **[report_follow_up]** follow_ups: Episode recall returned only unrelated shared-global advisory memory and was not used as evidence.
- **[research_citation]** sources: Sibling lifecycle spec: Requires dry-run defaults, path confinement, delete-time symlink refusal, grace handling, batch failure reporting, and non-local MCP dry-run coercion; local reviewed copy: docs/specs/lgrepSemanticCacheLifecycle.md requirements rq-prune-dry-run-default through rq-prune-response-contract. (https://github.com/Sharper-Flow/lgrep/blob/main/docs/specs/lgrepSemanticCacheLifecycle.md)
- **[research_citation]** sources: Current prune implementation: Current template admits only hash-shaped candidates, refuses scan and delete-time symlinks, checks resolved-path confinement, applies grace, and records per-entry deletion failures; local reviewed copy: src/lgrep/tools/prune_orphans.py:23-55, 231-281, 284-376. (https://github.com/Sharper-Flow/lgrep/blob/main/src/lgrep/tools/prune_orphans.py)
- **[research_citation]** sources: Symbol-store implementation: Store writes one JSON file per repository as index_<hash16>.json using temp-plus-rename; local repo keys resolve to paths while github: keys remain symbolic; local reviewed copy: src/lgrep/storage/index_store.py:45-59, 88-123, 125-158. (https://github.com/Sharper-Flow/lgrep/blob/main/src/lgrep/storage/index_store.py)
- **[research_citation]** sources.omitted: 3 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: WORKING DIRECTORY: /home/jon/dev/lgrep
CHANGE: addSymbolStorePrune | Add symbol store prune
SCOPE KEY: researcher:design-validation
ATTEMPT: 1
TASK_SCOPE: validate the proposed design against agreement, specs, and external evidence
IN_SCOPE: design, agreement constraints, sibling lifecycle spec, prune_orphans source pattern.
OUT_OF_SCOPE: rewriting design, unapproved scope, user-value tradeoffs.
DONE_WHEN: sourced Architecture Judgement or explicit inconclusive notes.
STOP_WHEN: contract/security/release blocker or conflict requiring decision.
VERIFICATION: official/source evidence per material claim.

Sibling module and per-file byte accounting fit separated semantic-cache and JSON symbol-store shapes. But C1 requires a mutex, while design defines neither lock nor shared writer/pruner critical section. POSIX unlink-while-open does not close scan-to-delete races; a prune-only lock would not coordinate with IndexStore.save atomic rename. Existing spec law does not conflict: semantic lifecycle governs cache directories and excludes symbols; a sibling capability is compatible. I do not know whether omitted verbatim AC1-AC11 impose additional response/scheduling requirements.
- **[unresolved_action]** validation.blockers: C1 requires a mutex as part of prune-orphans guard parity, but design names neither a lock nor shared writer/pruner protocol. IndexStore.save writes and atomically renames index files (src/lgrep/storage/index_store.py:93-123); a scan then unlink can race a writer. Existing storage uses dedicated lock file + fcntl.flock for multi-process mutation (src/lgrep/storage/_chunk_store.py:197-270).
- **[report_follow_up]** follow_ups: Harden: implement edge-case tests for _classify JSON shapes ([1,2,3], '"str"', '42', empty file, repo_path:null, repo_path:123)
- **[report_follow_up]** follow_ups: Harden: import _DEFAULT_SYMBOLS_DIR from IndexStore to prevent drift
- **[report_follow_up]** follow_ups: Harden: add TestCLIDispatch.test_main_dispatches_to_prune_symbols parity test
- **[report_follow_up]** follow_ups: Harden: rewrite rq-2bF6tR8nKp to remove SMELL_TOTALITY (specific measurable criteria)
- **[report_follow_up]** follow_ups: Backlog: structlog per-event logging across BOTH prune_symbols and prune_orphans (parity fix)
- **[report_follow_up]** follow_ups: Backlog: gc --symbols-dir passthrough (separate ergonomics change)
- **[report_follow_up]** follow_ups: Release-prep: CHANGELOG entry for new prune-symbols capability
- **[archive_only_evidence]** findings: [info] contract-traceability: Scanner-1 raised 'spec capability lgrepSymbolStoreLifecycle missing on disk' as blocker. Rejected_with_evidence: ADV delta model is change-owned (deltas on change record via adv_delta_add) and archive-applied; on-disk spec files appear at archive time by design, not before.
- **[archive_only_evidence]** findings: [suggestion] tests-tdd-evidence: Edge-case test coverage gaps for _classify JSON-shape tolerance: non-object JSON, zero-byte file, repo_path:null, non-string repo_path. Code paths correct but unverified.
- **[archive_only_evidence]** findings: [suggestion] correctness-edge-cases: _DEFAULT_SYMBOLS_DIR duplicated from IndexStore.__init__ default; drift risk if IndexStore changes
- **[archive_only_evidence]** findings: [suggestion] tests-tdd-evidence: Missing TestCLIDispatch.test_main_dispatches_to_prune_symbols (parity with test_main_dispatches_to_prune_orphans)
- **[archive_only_evidence]** findings: [suggestion] scope-conformance: rq-2bF6tR8nKp (dry-run-default spec delta) flagged SMELL_TOTALITY by adv_change_validate
- **[archive_only_evidence]** findings: [info] correctness-edge-cases: skipped_active computation does second full iterdir()+JSON parse (performance; parity with prune_orphans same pattern)
- **[archive_only_evidence]** findings: [info] correctness-edge-cases: unlink uses entry_path (unresolved); add clarifying comment that symlink guard already prevented dereference
- **[archive_only_evidence]** findings: [info] correctness-edge-cases: active_set entries not normalized through normalize_repo_key; mild duplicate-listing risk if caller passes mixed spellings
- **[archive_only_evidence]** findings: [info] security: Refused-symlink/out-of-root events not structlog-logged (parity with prune_orphans; both-path backlog item)
- **[archive_only_evidence]** findings: [info] scope-conformance: _cmd_gc doesn't forward --storage-dir to prune_symbols (explicitly OUT OF SCOPE per proposal)
- **[archive_only_evidence]** findings: [info] scope-conformance: CHANGELOG.md doesn't mention new capability (out of scope; release-prep convention)
- **[archive_only_evidence]** findings: [info] correctness-edge-cases: missing_repo_path_field collapses 4 JSON shapes into one reason (parity preserved; splitting would diverge from C1)
- **[archive_only_evidence]** findings: [info] tests-tdd-evidence: test_gc_preserves_existing_key_behavior doesn't pin shape of existing reports (mocks not the contract)

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| C1 | constraint | respected |
| C2 | constraint | pass |
| C3 | constraint | pass |
| C4 | constraint | pass |
| C5 | constraint | pass |
| C6 | constraint | pass |
| C7 | constraint | pass |
| C8 | constraint | pass |
| DONT1 | avoidance | respected |
| DONT2 | avoidance | respected |
| DONT3 | avoidance | respected |
| DONT4 | avoidance | respected |
| DONT5 | avoidance | respected |
| OOS1 | out_of_scope | missing |
| OOS2 | out_of_scope | missing |
| OOS3 | out_of_scope | missing |
| OOS4 | out_of_scope | missing |
| OOS5 | out_of_scope | missing |
| OOS6 | out_of_scope | missing |
| OOS7 | out_of_scope | missing |

## Unresolved Actions

- Run the requested `adv_task_checkpoint taskId:tk-9e4df88dd659 mode:complete` from the orchestrator side; it is not available to this leaf agent.
- finish_owned_scope_then_report: tests/test_server_registration.py was not in the original allowed file list, but updating it was unavoidable for the full-suite 0-failures requirement. It is a direct sibling test of the registration behavior being changed.
- verification_missing: No durable adv_run_test evidence found for run_id: manual-red-phase
- verification_missing: No durable adv_run_test evidence found for run_id: manual-lint
- C1 requires a mutex as part of prune-orphans guard parity, but design names neither a lock nor shared writer/pruner protocol. IndexStore.save writes and atomically renames index files (src/lgrep/storage/index_store.py:93-123); a scan then unlink can race a writer. Existing storage uses dedicated lock file + fcntl.flock for multi-process mutation (src/lgrep/storage/_chunk_store.py:197-270).
