# Archive Briefing Digest

**Change ID:** gateRemainingDestructiveMcp
**Title:** Gate remaining destructive MCP tools
**Status:** archived
**Generated:** 2026-07-30T20:34:50.238Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY
- Origin: discovery

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

Showing 50 of 50 durable facts.

- **[archive_only_evidence]** decisions: Store startup transport in bootstrap._startup_transport and read it lazily inside lifecycle._startup — Removes the LGREP_TRANSPORT environment side channel while preserving DiagnosticsResult.transport and avoiding circular imports (lifecycle imports bootstrap only inside _startup)
- **[archive_only_evidence]** decisions: Keep existing per-tool refusal messages but add structural registry-wide grant coverage — Prune tools have CLI equivalents; invalidation tools do not. Sharing CLI wording across all four would be misleading, so assertions are tool-group specific while destructive population is registry-derived
- **[archive_only_evidence]** decisions: Update .adv/specs/lgrepSemanticCacheLifecycle/spec.json instead of only markdown — That JSON still carried the stale LGREP_TRANSPORT wording and transport-inference rule; correcting the live spec means updating the persisted spec artifact
- **[archive_only_evidence]** verification: .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_server_registration.py tests/test_maintenance_grant.py tests/test_diagnostics.py -q (1) — RED phase: 5 bootstrap tests failed as expected because bootstrap/lifecycle still used LGREP_TRANSPORT env side channel
- **[archive_only_evidence]** verification: .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_server_registration.py tests/test_maintenance_grant.py tests/test_diagnostics.py tests/test_cli.py tests/test_server.py tests/test_server_tools.py -q (0) — GREEN phase: 181 focused tests passed after switching transport to bootstrap module attribute
- **[archive_only_evidence]** verification: .venv/bin/ruff check src/lgrep/server/bootstrap.py src/lgrep/server/lifecycle.py tests/test_bootstrap.py tests/test_server_registration.py tests/test_maintenance_grant.py tests/test_diagnostics.py && .venv/bin/ruff format src/lgrep/server/bootstrap.py src/lgrep/server/lifecycle.py tests/test_bootstrap.py tests/test_server_registration.py tests/test_maintenance_grant.py tests/test_diagnostics.py (0) — Ruff check and format clean on all touched Python files
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7weomn_5ffa5db7
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wjgsm_484466c1
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: ruff-scope
- **[archive_only_evidence]** decisions: Strengthen top-level test_destructive_allowed_with_grant_deletes_fixtures to create orphan cache and stale symbol index fixtures and assert real deletion — AC3 requires actual destructive behavior on all four tools under grant, not merely the absence of a refusal. The existing top-level parametrized test was the natural place to extend rather than adding a new duplicate.
- **[archive_only_evidence]** decisions: Removed the weaker granted-path tests from TestStructuralDestructiveGrantCoverage — They duplicated the top-level prune test and the TestRemainingDestructiveGrants invalidate tests; avoiding duplication keeps the structural class focused on registry pinning and refusal wording.
- **[archive_only_evidence]** verification: .venv/bin/python -m pytest tests/test_maintenance_grant.py tests/test_server_registration.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_cli.py tests/test_server.py tests/test_server_tools.py -q (0) — GREEN: 177 focused tests passed including actual fixture deletion for prune_orphans and prune_symbols with grant
- **[archive_only_evidence]** verification: .venv/bin/ruff check tests/test_maintenance_grant.py && .venv/bin/ruff format tests/test_maintenance_grant.py (0) — Ruff check and format clean on touched test file
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wrup7_4e71658a
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: ruff-scope-2
- **[archive_only_evidence]** decisions: Removed the LGREP_TRANSPORT row from the README environment-variable table — Runtime now ignores LGREP_TRANSPORT; documenting it as an auto-set setting would be a misleading public side channel. Historical CHANGELOG entry left untouched.
- **[archive_only_evidence]** verification: .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_maintenance_grant.py tests/test_diagnostics.py tests/test_server_registration.py -q (0) — Focused grant/registry/bootstrap/diagnostics tests still pass after README edit
- **[archive_only_evidence]** verification: .venv/bin/ruff check tests/test_maintenance_grant.py src/lgrep/server/bootstrap.py src/lgrep/server/lifecycle.py (0) — Ruff clean on previously touched source/test files
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wvav5_6affb995
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: ruff-docs-3
- **[unresolved_action]** required_main_agent_actions: Checkpoint the reviewer test-only change on task tk-7d4722205f39 before marking review work complete.
- **[unresolved_action]** required_main_agent_actions: After merge and deployment, perform AC7 managed endpoint proof for invalidate_cache and invalidate_worktree_cache without the grant, then verify service health.
- **[unresolved_action]** required_main_agent_actions: Leave core tools, CLI behavior, Vision topology, and the already-reviewed grant implementation unchanged.
- **[wisdom_candidate]** wisdom_candidates: [convention] For destructive-handler tests, construct a real isolated target before denial and assert its exact filesystem path after both denial and granted execution. API status or list visibility alone does not prove no deletion or deletion.
- **[archive_only_evidence]** changes_made: tests/test_maintenance_grant.py: Strengthened invalidate_cache grant tests to prove a pre-existing isolated symbol-index fixture remains on refusal and its exact index file is deleted after a granted call; closes AC2/AC3 evidence gaps without changing runtime behavior.
- **[archive_only_evidence]** verification: tests_run=.venv/bin/python -m pytest tests/test_maintenance_grant.py, .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py, .venv/bin/python -m pytest, .venv/bin/python -m ruff check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py, .venv/bin/python -m ruff format --check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py, git diff --check origin/main...HEAD, rg static scan for runtime os.environ LGREP_TRANSPORT reader/writer in src results=pass — Focused grant suite: 16 passed. Bootstrap/diagnostics/registration suite: 20 passed. Full suite: 736 passed in 43.26s (16 pre-existing dependency/fork deprecation warnings). Ruff check/format passed. Diff whitespace check passed. Static source scan found no runtime os.environ reader/writer for LGREP_TRANSPORT. Review found exact four destructiveHint tools, handler-layer grant guards, correct no-CLI invalidation refusals, preserved diagnostics transport via bootstrap attribute/app context, and no import-cycle failure.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest tests/test_maintenance_grant.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m ruff check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m ruff format --check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check origin/main...HEAD
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: rg static scan for runtime os.environ LGREP_TRANSPORT reader/writer in src
- **[report_follow_up]** follow_ups: File a backlog item for optional CLI commands 'lgrep invalidate-cache'/'lgrep invalidate-worktree' to give operators a local escape hatch matching prune_* (proposal User Outcome #2 currently unfulfillable for 2/4 tools).
- **[report_follow_up]** follow_ups: Consider a follow-up to make core invalidate_cache honor LGREP_SYMBOLS_DIR like prune_symbols does, removing the env-isolation asymmetry and the test need to monkeypatch DEFAULT_SYMBOLS_DIR.
- **[report_follow_up]** follow_ups: Note pre-existing inconsistency: invalidate_cache MCP handler bypasses the RuntimeSupervisor (asyncio.to_thread, no ctx, no cancellation/active-job visibility) unlike the maintenance tools that use _run_blocking — out of scope here but worth a backlog note.
- **[report_follow_up]** follow_ups: Confirm at execution time that mcp._tool_manager.list_tools() returns the FunctionTool with .annotations in the actually-installed 1.x patch (verified against v1.12.1 upstream base.py; lgrep pins mcp>=1.28,<2).
- **[research_citation]** sources: Grant helper + gated tools (prune_orphans, prune_symbols) and UNGATED invalidate_worktree_cache handler: _DESTRUCTIVE_GRANT_ENV/_destructive_grant_present()/_refusal_reason() defined here; called only at :122 (prune_orphans) and :176 (prune_symbols). invalidate_worktree_cache (:201 destructiveHint=True, :207-248) calls _invalidate_worktree_cache unconditionally with NO grant check. Its MCP description (:195-197) says 'Does NOT delete the canonical LanceDB cache' contradicting its own docstring (:214-219 'deletes the cache dir') and shutil.rmtree at invalidate_worktree.py:207. (src/lgrep/server/tools_maintenance.py:58-82,85-136,139-190,193-248)
- **[research_citation]** sources: UNGATED invalidate_cache MCP handler (destructiveHint=True, no ctx, no dry_run): invalidate_cache(path)->InvalidateCacheResult has NO ctx param and NO dry_run; calls asyncio.to_thread(_invalidate_cache,path) directly with no grant check. Reaches store.delete_index -> index_file.unlink (index_store.py:209). (src/lgrep/server/tools_symbols.py:581-615)
- **[research_citation]** sources: delete_index unlinks; IndexStore ignores LGREP_SYMBOLS_DIR: IndexStore.__init__ uses DEFAULT_SYMBOLS_DIR when storage_dir is None; does NOT read LGREP_SYMBOLS_DIR. delete_index calls index_file.unlink(missing_ok=True). So the MCP invalidate_cache(path) handler (passes no storage_dir) always targets DEFAULT_SYMBOLS_DIR and cannot be isolated via LGREP_SYMBOLS_DIR env. _cache is a ClassVar dict (line 89) -> cross-test pollution hazard. (src/lgrep/storage/index_store.py:91-105,200-213)
- **[research_citation]** sources.omitted: 8 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: All four destructiveHint=True MCP tools confirmed: prune_orphans (tools_maintenance.py:96) and prune_symbols (:150) are GATED via _destructive_grant_present() (:122,:176); invalidate_cache (tools_symbols.py:589) and invalidate_worktree_cache (tools_maintenance.py:201) are UNGATED and reach real deletion (index_store.py:209 unlink; invalidate_worktree.py:207 rmtree). The proposal is sound and the in-repo v3.2.2 precedent (fixLookupPruneDefects, .adv/archive/2026-07-30-fixLookupPruneDefects) proves the grant pattern end-to-end. Four design-shaping issues must be resolved at implementation time, none blocking: (1) the two ungated tools have NO dry_run/preview param, so the refusal shape is no-op+refused_reason, NOT the 'preview-plus-refused_reason' the proposal names; (2) no CLI equivalent exists for invalidate_* so the proposal's User Outcome #2 ('what the local equivalent is') is unfulfillable for 2 of 4 tools; (3) transport removal is NOT a clean 2-line delete — the only runtime consumer is the diagnostics echo (tools_diagnostics.py:68,102) into DiagnosticsResult.transport, and the proposal's own constraint ('additive only, do not remove/retype fields') means removal must REPIPE transport via a non-env bridge or explicitly handle the field; (4) concrete test collisions + fixture-isolation specifics (test_server.py:297-320 breaks; LGREP_SYMBOLS_DIR does NOT isolate invalidate_cache because IndexStore ignores it — must monkeypatch DEFAULT_SYMBOLS_DIR; IndexStore._cache ClassVar hazard; granted tests for prune_* currently assert only dry_run=False and should be strengthened to assert real deletion).
- **[report_follow_up]** follow_ups: Spec drift: rq-prune-mcp-transport-safety (lgrepSemanticCacheLifecycle spec.json:287) references LGREP_TRANSPORT and transport-based coercion; both stale after v3.2.2 (grant-based) and definitively stale after AC6. Separate spec-correction change recommended.
- **[report_follow_up]** follow_ups: README:450 LGREP_TRANSPORT row documents the env var as auto-set; after AC6 runtime no longer sets it. Update or remove in this change's README pass.
- **[report_follow_up]** follow_ups: Guard against tool.annotations being None in the structural registry test so a future tool registered without annotations does not AttributeError.
- **[research_citation]** sources: FastMCP ToolManager API (official MCP Python SDK docs): list_tools() is synchronous and returns list[Tool]; add_tool stores annotations=ToolAnnotations; MCPServer.list_tools reads info.annotations off the Tool objects. Confirms the structural-test approach and the existing test's _tool_manager.list_tools() usage. (https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/fastmcp/tools/tool_manager.py)
- **[research_citation]** sources: local: lgrep server grant handler: _destructive_grant_present() at :61 reads LGREP_ALLOW_DESTRUCTIVE_MCP; called only at :122 (prune_orphans) and :176 (prune_symbols). _refusal_reason(cli_command) at :77 hard-codes 'Returned a preview instead' and 'run {cli_command} locally' — cannot be reused for invalidation tools. invalidate_worktree_cache at :207 has no grant check and description at :195 wrongly says 'Does NOT delete the canonical LanceDB cache'. (/home/jon/dev/lgrep/src/lgrep/server/tools_maintenance.py)
- **[research_citation]** sources: local: invalidate_cache handler: invalidate_cache at :581 declares destructiveHint=True (:589) and calls asyncio.to_thread(_invalidate_cache, path) at :611 with no grant check and no ctx param. (/home/jon/dev/lgrep/src/lgrep/server/tools_symbols.py)
- **[research_citation]** sources.omitted: 5 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The design is architecturally sound and consistent with the established v3.2.2 grant pattern: handler-layer _destructive_grant_present() predicate (tools_maintenance.py:61) is the right authority locus, and extending it to the two ungated destructive handlers preserves the core/CLI-grant-free invariant. The refusal shapes (additive NotRequired[str] refused_reason on existing schemas; status='refused' for invalidate_cache since its status field is typed str, not Literal; zero-effect WorktreeInvalidationResult for worktree) are schema-valid and additive per the constraint. The structural-population proof is viable: the FastMCP 1.x ToolManager.list_tools() is synchronous and returns Tool objects exposing .annotations (verified against official SDK docs and the existing passing test at test_maintenance_grant.py:23), and exactly four tools declare destructiveHint=True. Fixture isolation is verified consistent: both cache-path resolvers read LGREP_CACHE_DIR, and DEFAULT_SYMBOLS_DIR is the live IndexStore default. The transport repipe is cycle-free and preserves DiagnosticsResult.transport. Deviation from by-the-book: MINOR — all deviations are implementer guardrails, none are design contradictions. No CONFLICTs found.

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| AC6 | acceptance_criterion | pass |
| AC7 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| C4 | constraint | respected |
| C5 | constraint | respected |
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |
| OOS3 | out_of_scope | not_applicable |
| OOS4 | out_of_scope | not_applicable |

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7weomn_5ffa5db7
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wjgsm_484466c1
- verification_missing: No durable adv_run_test evidence found for run_id: ruff-scope
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wrup7_4e71658a
- verification_missing: No durable adv_run_test evidence found for run_id: ruff-scope-2
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms7wvav5_6affb995
- verification_missing: No durable adv_run_test evidence found for run_id: ruff-docs-3
- Checkpoint the reviewer test-only change on task tk-7d4722205f39 before marking review work complete.
- After merge and deployment, perform AC7 managed endpoint proof for invalidate_cache and invalidate_worktree_cache without the grant, then verify service health.
- Leave core tools, CLI behavior, Vision topology, and the already-reviewed grant implementation unchanged.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest tests/test_maintenance_grant.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m pytest
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m ruff check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: .venv/bin/python -m ruff format --check src/lgrep/server tests/test_maintenance_grant.py tests/test_bootstrap.py tests/test_diagnostics.py tests/test_server_registration.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check origin/main...HEAD
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: rg static scan for runtime os.environ LGREP_TRANSPORT reader/writer in src
