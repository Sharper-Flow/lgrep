# Archive Briefing Digest

**Change ID:** fixDeployedLgrepDefects
**Title:** Fix deployed lgrep defects
**Status:** archived
**Generated:** 2026-07-31T16:56:16.753Z

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

Showing 76 of 76 durable facts.

- **[archive_only_evidence]** decisions: Made lgrep.tools._meta.make_meta the single metadata producer and required a tool name argument. — The design calls for one declared response envelope and one producer; every MCP success path now uses the same _meta shape (tool, timing_ms, tokens_saved, session_tokens, total_tokens, cost_avoided_usd).
- **[archive_only_evidence]** decisions: Updated responses._Meta to the token-tracker envelope and moved it above referencing TypedDicts. — Keeps the canonical schema declared once and ensures all maintenance/symbol response types reference the same envelope. FastMCP still does not emit underscore-prefixed fields in its JSON schema, so the test also asserts the envelope at the raw dict layer.
- **[archive_only_evidence]** decisions: Changed maintenance response refused_reason from NotRequired[str] to required-nullable str | None. — AC1/AC2 require the field on every path; null is emitted on success/refusal paths so clients never see an omitted required property.
- **[archive_only_evidence]** decisions: Replaced hardcoded {'duration_ms': 0.0, 'tool': ...} literals in symbol handlers with make_meta(time.monotonic(), tool_name). — Eliminates the placeholder meta that previously produced a divergent MCP-layer contract from the direct-tool contract.
- **[archive_only_evidence]** decisions: Validated via tool.run(..., convert_result=True) in the new regression test. — This exercises FastMCP's actual structured-output validation path, not just the raw handler return value.
- **[archive_only_evidence]** verification: python -m pytest tests/test_mcp_response_contract.py -v (1) — Red phase: new FastMCP contract regression test failed as expected because maintenance tools returned responses missing required refused_reason/_meta fields.
- **[archive_only_evidence]** verification: python -m pytest tests/test_mcp_response_contract.py::test_maintenance_tool_validates_on_default_path tests/test_mcp_response_contract.py::test_maintenance_tool_refusal_path_validates_and_names_grant -v (0) — Green phase: maintenance tools now pass FastMCP structured-output validation and emit canonical _meta envelope and nullable refused_reason on both default and refusal paths.
- **[archive_only_evidence]** verification: python -m pytest tests/test_mcp_response_contract.py tests/test_server_tools.py tests/test_maintenance_grant.py tests/test_server_registration.py tests/test_symbol_tools.py tests/test_diagnostics.py tests/test_runtime_cancellation.py (0) — Focused verification: 156 contract, maintenance, symbol-tool, diagnostics, and cancellation tests pass after unifying meta producer.
- **[archive_only_evidence]** verification: python -m pytest tests (0) — Full project regression: 749 tests passed, 16 pre-existing warnings; no new failures attributable to this change.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms897b8i_7b931189
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms89sobp_ab189203
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8a5mg8_25b04708
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8a8m1h_dc3a6382
- **[archive_only_evidence]** decisions: Switched package version from a static pyproject.toml field to Hatch VCS — Hatch VCS derives the version from the release tag, eliminating the manual version bump drift that caused the v3.2.3 tag to ship with a 3.2.2 artifact.
- **[archive_only_evidence]** decisions: Reordered the auto-release workflow so CHANGELOG is committed and the tag is created before the build — Hatch VCS needs the release tag to exist at build time; building before tagging produced artifacts with the previous version.
- **[archive_only_evidence]** decisions: Added build/hatch-vcs to dev dependencies and used importlib.metadata as the runtime __version__ source — Lets tests and the CLI read the version installed by the build backend, while keeping a safe fallback for non-installed source execution.
- **[archive_only_evidence]** decisions: Set CI fetch-depth to 0 so tags are available in non-release builds — Defines non-release CI behavior: version is derived from the latest tag rather than a fallback/no-version state.
- **[archive_only_evidence]** verification: python -m pytest tests/test_version.py -v (1) — Red phase: new tag-derived version test fails because __version__ is still static 3.2.2 while the latest tag is v3.2.3.
- **[archive_only_evidence]** verification: python -m pytest tests/test_version.py -v (0) — Green phase: all four tag/build/fallback tests pass after implementing Hatch VCS and workflow reordering.
- **[archive_only_evidence]** verification: python -m pytest tests/test_version.py tests/test_deps.py::test_version_is_dynamic tests/test_cli.py::TestCLIDispatch::test_main_version -v (0) — Verify phase: version tests, dynamic-version config test, and CLI --version test all pass.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8akflj_1bfc5e01
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8atfmj_79f49567
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8ay3ev_da5f6e46
- **[archive_only_evidence]** decisions: Changed runtime __version__ resolution to prefer the Hatch VCS generated _version.py, then a git tag-derived version, then installed package metadata. — When tests run with PYTHONPATH=src, importlib.metadata still sees the host-installed lgrep 3.2.2. Deriving the version from the nearest reachable release tag isolates the worktree runtime from stale host metadata while preserving tag-derived releases.
- **[archive_only_evidence]** decisions: Updated test_version_matches_package_metadata to compare against importlib.metadata only when the metadata belongs to the imported package tree. — A host-installed distribution is unrelated to a source-tree run; asserting equality would force the test to fail whenever the host lags the worktree tag.
- **[archive_only_evidence]** verification: PYTHONPATH=/home/jon/.local/share/opencode/worktree/6f85aebf461c84fa97e1d1570b32ec83fa191248/change/fixDeployedLgrepDefects/src /home/jon/dev/lgrep/.venv/bin/python -m pytest tests/test_version.py -v (0) — All 4 version tests pass: tag base match, metadata isolation, wheel tag version, and fallback version.
- **[archive_only_evidence]** verification: /home/jon/dev/lgrep/.venv/bin/ruff check src/lgrep/__init__.py tests/test_version.py (0) — Ruff lint clean on changed files.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dx75u_4fc04213
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dwxp8_1013e3df
- **[archive_only_evidence]** decisions: Introduced IndexWindowResult and index_window API — Design requires bounded windows that report remaining work instead of raising on wall budget; enables lifecycle to resume from pending files
- **[archive_only_evidence]** decisions: Made index_all loop bounded windows until convergence — Preserves CLI/back-compat while ensuring a single call converges for repositories larger than one budget window
- **[archive_only_evidence]** decisions: Stored pending_index_files in ProjectState and scheduled a single background continuation — Initial search gets state back immediately; one background task processes remaining windows so search never waits
- **[archive_only_evidence]** decisions: Added batched hash projection via compute_pending_files + get_file_hashes — Avoids one DB query per file at index-start; deterministic pending list supports resume
- **[archive_only_evidence]** decisions: Extended staleness detection to flag never-indexed files — Partial windows can leave files older than latest_indexed_at; without this check they would look fresh
- **[archive_only_evidence]** decisions: Persisted zero-chunk file tracking in ChunkStore — Never-indexed detection would otherwise force repeated reindex of files that intentionally produce no chunks
- **[archive_only_evidence]** verification: python -m pytest tests/test_index_window_convergence.py -v (2) — Red phase: new convergence tests fail to import because IndexWindowResult/index_window do not exist yet
- **[archive_only_evidence]** verification: python -m pytest tests (0) — Full suite passes: 760 passed, 17 warnings
- **[archive_only_evidence]** verification: ruff check src tests (0) — Ruff lint passes with no errors
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8bm4mr_7ac1398e
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8di8ew_c1053a1b
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dfpxl_fd537fdb
- **[archive_only_evidence]** verification: ruff format --check src/lgrep/indexing.py src/lgrep/server/lifecycle.py tests/test_index_window_convergence.py (0) — Ruff format check passes: 3 files already formatted
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8e5kfp_083d2c6e
- **[report_follow_up]** follow_ups: Confirm whether deployed v3.2.3 responses.py actually lacked NotRequired (vs a typing_extensions/typing.NotRequired + from__future__import_annotations interaction) via git-archaeology at the v3.2.3 tag; design robustness must not depend on the answer, but it explains why NotRequired did not save the deployed build.
- **[report_follow_up]** follow_ups: Verify ci.yml does not invoke a tagless/shallow build that breaks under hatch-vcs without fallback-version; add fallback-version or ensure fetch-depth:0 wherever the version is resolved.
- **[report_follow_up]** follow_ups: At execution, assert via the FastMCP structured-output test that prune_orphans/prune_symbols/invalidate_worktree_cache each return a null refused_reason that validates, and that get_symbols/search_references _meta still validates after the timing_ms/duration_ms reconciliation.
- **[research_citation]** sources: FastMCP func_metadata.py (mcp 1.29.0) convert_result runs model_validate: convert_result() calls self.output_model.model_validate(result) whenever output_schema is set. Runtime output validation IS performed; contradicts responses.py docstring. (file://.venv/lib/python3.13/site-packages/mcp/server/fastmcp/utilities/func_metadata.py#L98-132)
- **[research_citation]** sources: FastMCP func_metadata.py _create_model_from_typeddict uses __required_keys__: Required keys get type-only fields (required, no default); non-required get (type, None). get_type_hints strips NotRequired to str. (file://.venv/lib/python3.13/site-packages/mcp/server/fastmcp/utilities/func_metadata.py#L463-481)
- **[research_citation]** sources: FastMCP server.py convert_result=True in live path: FastMCP.call_tool invokes tool_manager.call_tool(..., convert_result=True). Validation runs on every tool result. (file://.venv/lib/python3.13/site-packages/mcp/server/fastmcp/server.py#L346-349)
- **[research_citation]** sources.omitted: 10 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The design's three pillars are directionally correct and each traces to a real defect confirmed in source. (1) MCP contract: the central claim that FastMCP performs runtime output validation is TRUE and the responses.py docstring claiming otherwise is FALSE. Chain: handler omits refused_reason on normal dry-run (tools_maintenance.py:134-136,188-190); _create_model_from_typeddict treats it as required (func_metadata.py:463-481, using __required_keys__, get_type_hints strips NotRequired to str); convert_result runs model_validate (func_metadata.py:129) because the live call path passes convert_result=True (server.py:349); Tool.run wraps the resulting ValidationError as ToolError (base.py:117) which is exactly the observed 'refused_reason Field required' rejection. (2) Release identity: static pyproject version (pyproject.toml:3) + build step running BEFORE tag creation (auto-release.yml:107 builds, :113 commits+tags) is the confirmed root cause; hatch-vcs + tag-before-build is the canonical fix and matches upstream config (/ofek/hatch-vcs). (3) Index convergence: replacing raise-on-budget (indexing.py:124-130) with a bounded windowed result + single-flight continuation fits the existing lifecycle/_bg_reindex_tasks/_indexing_events architecture. However pillar 1 has one correctness-critical incompleteness: it mandates emitting refused_reason as null 'when absent' but does not require the declared type to become nullable (str | None). Given Pydantic v2 semantics and the confirmed required-field mechanism, emitting null against the current NotRequired[str]/str annotation would itself fail validation, so AC1 would not be satisfied as literally written. Secondary under-specifications: timing_ms vs duration_ms reconciliation; per-window >=1-file non-convergence guard; hatch-vcs build-requires/dynamic/fallback-version.
- **[unresolved_action]** validation.blockers: B1 (in_scope, AC1/AC2): The design's pillar-1 fix mandates emitting refused_reason as null 'when absent' but does not require the declared field type to become nullable. Confirmed mechanism: the deployed schema treats refused_reason as REQUIRED (the defect 'refused_reason Field required' is observed), and per Pydantic v2 semantics a str/NotRequired[str] field does not accept None as an input value (default None applies only on omission). Therefore emitting null against the current NotRequired[str]/str annotation would produce 'Input should be a valid string' and AC1/AC2 would still fail. Required correction: in src/lgrep/server/responses.py change refused_reason on InvalidateCacheResult, PruneOrphansResult, PruneSymbolsResult, WorktreeInvalidationResult from NotRequired[str] to Required[str | None] (required, nullable), and ensure every handler path sets it to None when not refused - including prune_orphans/prune_symbols (tools_maintenance.py:134-136,188-190) which currently add it only on the refusal path. State this invariant explicitly in design section 1.
- **[unresolved_action]** required_main_agent_actions: Review and checkpoint the two scoped remediation files before completing acceptance evidence.
- **[unresolved_action]** required_main_agent_actions: Do not revisit unrelated token/performance redesigns, MCP-server replacement, deployment, or global index-budget changes; they remain out of scope.
- **[wisdom_candidate]** wisdom_candidates: [gotcha] When a TypedDict response contract gains required fields, audit timeout and cancellation fallbacks in shared decorators; normal-path FastMCP validation does not exercise those envelopes.
- **[archive_only_evidence]** changes_made: src/lgrep/server/__init__.py: Completed the search_references timeout and cancellation fallbacks with the five required count fields so the returned envelope matches SearchReferencesResult.
- **[archive_only_evidence]** changes_made: tests/test_server_tools.py: Expanded cancellation coverage and added a timeout regression test for the complete search_references structured response shape.
- **[archive_only_evidence]** verification: tests_run=VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q tests/test_server_tools.py tests/test_mcp_response_contract.py tests/test_index_window_convergence.py tests/test_runtime_cancellation.py tests/test_version.py, VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q, python -m ruff check ., python -m ruff format --check ., EXPECTED=$(git describe --tags --abbrev=0 | cut -c2-); ACTUAL=$(python -m lgrep.cli --version | awk '{print $2}'); test "$ACTUAL" = "$EXPECTED" results=pass — Focused suite: 56 passed. Full suite: 761 passed in 85.16s. Ruff check and format check passed. CLI version equals nearest tag: expected=3.2.3 actual=3.2.3. Diff whitespace checks passed. Initial review found search_references timeout/cancellation outputs omitted five required SearchReferencesResult counters; remediation and regression coverage are included above.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q tests/test_server_tools.py tests/test_mcp_response_contract.py tests/test_index_window_convergence.py tests/test_runtime_cancellation.py tests/test_version.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check .
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check .
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: EXPECTED=$(git describe --tags --abbrev=0 | cut -c2-); ACTUAL=$(python -m lgrep.cli --version | awk '{print $2}'); test "$ACTUAL" = "$EXPECTED"
- **[unresolved_action]** required_main_agent_actions: Create the required ADV task checkpoint/commit for .github/workflows/auto-release.yml and CHANGELOG.md; reviewer cannot mutate ADV task state.
- **[unresolved_action]** required_main_agent_actions: Do not deploy from this worktree. Merge through the normal CI path; the Auto Release workflow remains the release authority.
- **[unresolved_action]** required_main_agent_actions: Optionally add actionlint to CI or a release-workflow validation job, and consider commit-SHA pinning GitHub Actions as a separate hardening follow-up.
- **[wisdom_candidate]** wisdom_candidates: [gotcha] Shell double quotes do not expand backslash-n. GitHub Actions release-note assembly must embed literal newlines (or use printf) rather than concatenating \\n into variables.
- **[archive_only_evidence]** changes_made: .github/workflows/auto-release.yml: Replaced literal backslash-n concatenation in generated release-note sections with real newlines, so future Auto Release changelog entries render valid Markdown.
- **[archive_only_evidence]** changes_made: CHANGELOG.md: Converted all nine existing generated entries containing literal backslash-n sequences to rendered Markdown headings and lists.
- **[archive_only_evidence]** verification: tests_run=oc-fresh status --repo /home/jon/.local/share/opencode/worktree/6f85aebf461c84fa97e1d1570b32ec83fa191248/change/fixDeployedLgrepDefects --json, python -m ruff check ., python -m ruff format --check ., python -m pytest, python -m build, python -m pytest tests/test_version.py && python -m build && ! rg -n '\\\\n' CHANGELOG.md .github/workflows/auto-release.yml results=pass — Worktree was clean and current against origin/main before review. Ruff check and format check passed. Full suite: 761 passed in 122.54s (17 existing dependency/runtime deprecation warnings). Isolated sdist and wheel builds succeeded. After remediation, targeted version tests: 4 passed; build succeeded; escaped-newline scan and shell newline construction assertion passed. actionlint is unavailable, so no dedicated workflow linter ran.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: oc-fresh status --repo /home/jon/.local/share/opencode/worktree/6f85aebf461c84fa97e1d1570b32ec83fa191248/change/fixDeployedLgrepDefects --json
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check .
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check .
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m build
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_version.py && python -m build && ! rg -n '\\\\n' CHANGELOG.md .github/workflows/auto-release.yml

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
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |
| OOS3 | out_of_scope | not_applicable |

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms897b8i_7b931189
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms89sobp_ab189203
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8a5mg8_25b04708
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8a8m1h_dc3a6382
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8akflj_1bfc5e01
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8atfmj_79f49567
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8ay3ev_da5f6e46
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dx75u_4fc04213
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dwxp8_1013e3df
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8bm4mr_7ac1398e
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8di8ew_c1053a1b
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8dfpxl_fd537fdb
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms8e5kfp_083d2c6e
- B1 (in_scope, AC1/AC2): The design's pillar-1 fix mandates emitting refused_reason as null 'when absent' but does not require the declared field type to become nullable. Confirmed mechanism: the deployed schema treats refused_reason as REQUIRED (the defect 'refused_reason Field required' is observed), and per Pydantic v2 semantics a str/NotRequired[str] field does not accept None as an input value (default None applies only on omission). Therefore emitting null against the current NotRequired[str]/str annotation would produce 'Input should be a valid string' and AC1/AC2 would still fail. Required correction: in src/lgrep/server/responses.py change refused_reason on InvalidateCacheResult, PruneOrphansResult, PruneSymbolsResult, WorktreeInvalidationResult from NotRequired[str] to Required[str | None] (required, nullable), and ensure every handler path sets it to None when not refused - including prune_orphans/prune_symbols (tools_maintenance.py:134-136,188-190) which currently add it only on the refusal path. State this invariant explicitly in design section 1.
- Review and checkpoint the two scoped remediation files before completing acceptance evidence.
- Do not revisit unrelated token/performance redesigns, MCP-server replacement, deployment, or global index-budget changes; they remain out of scope.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q tests/test_server_tools.py tests/test_mcp_response_contract.py tests/test_index_window_convergence.py tests/test_runtime_cancellation.py tests/test_version.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: VOYAGE_API_KEY=dummy-key-for-tests python -m pytest -q
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check .
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check .
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: EXPECTED=$(git describe --tags --abbrev=0 | cut -c2-); ACTUAL=$(python -m lgrep.cli --version | awk '{print $2}'); test "$ACTUAL" = "$EXPECTED"
- Create the required ADV task checkpoint/commit for .github/workflows/auto-release.yml and CHANGELOG.md; reviewer cannot mutate ADV task state.
- Do not deploy from this worktree. Merge through the normal CI path; the Auto Release workflow remains the release authority.
- Optionally add actionlint to CI or a release-workflow validation job, and consider commit-SHA pinning GitHub Actions as a separate hardening follow-up.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: oc-fresh status --repo /home/jon/.local/share/opencode/worktree/6f85aebf461c84fa97e1d1570b32ec83fa191248/change/fixDeployedLgrepDefects --json
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check .
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check .
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m build
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_version.py && python -m build && ! rg -n '\\\\n' CHANGELOG.md .github/workflows/auto-release.yml
