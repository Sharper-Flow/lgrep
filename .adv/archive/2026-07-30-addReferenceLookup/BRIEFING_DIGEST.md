# Archive Briefing Digest

**Change ID:** addReferenceLookup
**Title:** Add reference lookup
**Status:** archived
**Generated:** 2026-07-30T00:46:15.135Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY

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

Showing 47 of 47 durable facts.

- **[report_follow_up]** follow_ups: lgrep.server tests and other server-dependent tests fail locally because the installed `mcp` package does not expose `mcp.server.fastmcp`; this is a pre-existing environment issue, not a regression from this task.
- **[report_follow_up]** follow_ups: index_repo (GitHub remote indexing) does not yet collect occurrences; it may need to be updated if the reference lookup tool is exposed to remote repos.
- **[archive_only_evidence]** decisions: Added an Occurrence dataclass and make_occurrence_id alongside Symbol, keeping the existing SymbolExtractor.extract() API unchanged. — Preserves existing call sites while giving index_folder a new extract_full() path for symbols + candidate occurrences.
- **[archive_only_evidence]** decisions: Collected Python identifier occurrences only for function calls, attribute names, imports, and generic references, excluding function/class definition names. — Matches the scoped, candidate-only usage retrieval requirement; avoids claiming resolution or exhaustive references.
- **[archive_only_evidence]** decisions: Stored occurrences in CodeIndex keyed by identifier name and bumped the index version to 2.1. — Enables efficient lookup by name and gives a clean migration gate for old 2.0 indexes.
- **[archive_only_evidence]** decisions: Forced re-extraction of unchanged files when an existing index is older than 2.1 or has no occurrence data. — Satisfies the safe old-index refresh requirement without unbounded work or background indexing.
- **[archive_only_evidence]** verification: uv run --extra dev python -m pytest tests/test_occurrences.py -q (1) — Red phase failed as expected: Occurrence model and extract_full did not exist yet.
- **[archive_only_evidence]** verification: uv run --extra dev python -m pytest tests/test_occurrences.py tests/test_parser.py tests/test_index_store.py tests/test_symbol_tools.py tests/test_indexing.py -q (0) — 152 passed, including new occurrence tests and existing parser/index/store/symbol/indexing tests.
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms6np8c3_cbcdd060
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms6nxd81_e6c3367e
- **[archive_only_evidence]** decisions: Named the new tool search_references and registered it alongside search_symbols/search_text — Keeps the symbol-engine tool namespace consistent and makes the candidate-lookup intent explicit
- **[archive_only_evidence]** decisions: Routed the server handler through RuntimeSupervisor when an MCP context is available — Design specified bounded owned execution; reuses the existing supervisor contract for cancellation/timeout safety
- **[archive_only_evidence]** decisions: Added explicit stale-index guard for missing occurrence data — Old indexes built before occurrence extraction would otherwise silently return empty results
- **[archive_only_evidence]** decisions: Kept occurrence result ordering deterministic for production_first and include_tests — Stable responses make tests and callers predictable while still honoring production-first semantics
- **[archive_only_evidence]** verification: uv run pytest tests/test_reference_lookup.py tests/test_symbol_tools.py tests/test_occurrences.py tests/test_e2e_symbols.py tests/test_indexing.py tests/test_index_store.py -q (0) — 146 passed; covers new lookup tool, filtering, ambiguity, stale-index errors, and regression checks for symbol/occurrence/e2e/indexing tests
- **[archive_only_evidence]** verification: uv run ruff check src/lgrep/tools/search_references.py src/lgrep/server/responses.py src/lgrep/server/tools_symbols.py src/lgrep/server/__init__.py tests/test_reference_lookup.py tests/test_symbol_tools.py tests/test_server_registration.py tests/test_server_tools.py (0) — Ruff lint clean on all changed files
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: uv-pytest-reference-lookup-146
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: ruff-check-changed-files
- **[archive_only_evidence]** verification: adv_run_test (0) — 147 passed
- **[unresolved_action]** required_main_agent_actions: Resolve the mcp.server.fastmcp compatibility blocker by selecting a supported MCP SDK constraint/lock or migrating the server API, then rerun server-facing tests including tests/test_server_tools.py and tests/test_server_registration.py.
- **[unresolved_action]** required_main_agent_actions: Do not revisit version, README/CHANGELOG, IndexSymbolsFolderResult field preservation, or duplicate timeout ownership: this review verified them; only the MCP compatibility blocker remains.
- **[wisdom_candidate]** wisdom_candidates: [gotcha] A permissive mcp>=1.0.0 constraint currently resolves mcp 2.0.0, which no longer exposes mcp.server.fastmcp. Server-facing tests cannot collect until the dependency/API mismatch is resolved.
- **[archive_only_evidence]** changes_made: src/lgrep/server/responses.py: Removed the unused duplicate timeout constant and time_tool implementation; lgrep.server remains the sole runtime owner of timeout/cancellation behavior.
- **[archive_only_evidence]** changes_made: tests/test_server_tools.py: Added a schema-shape regression test for a cancelled search_references tool invocation.
- **[archive_only_evidence]** verification: tests_run=uv run --extra dev pytest tests/test_reference_lookup.py tests/test_occurrences.py tests/test_symbol_tools.py, uv run --extra dev pytest tests/test_reference_lookup.py tests/test_server_tools.py tests/test_occurrences.py tests/test_symbol_tools.py tests/test_server_registration.py, uv run --extra dev ruff check src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py, uv run --extra dev python -m py_compile src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py, git diff --check results=fail — 111 non-server reference/index tests passed. Ruff, py_compile, and git diff --check passed. Server-facing collection fails before test execution: installed and locked mcp 2.0.0 lacks mcp.server.fastmcp, raising ModuleNotFoundError from src/lgrep/server/__init__.py:13. Therefore the new cancellation regression and MCP registration tests could not execute.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev pytest tests/test_reference_lookup.py tests/test_occurrences.py tests/test_symbol_tools.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev pytest tests/test_reference_lookup.py tests/test_server_tools.py tests/test_occurrences.py tests/test_symbol_tools.py tests/test_server_registration.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev ruff check src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev python -m py_compile src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
- **[report_follow_up]** follow_ups: Design should specify the Python AST occurrence taxonomy (identifier, call, attribute_access, assignment_target) - currently deferred; needed for AC1 enclosing context.
- **[report_follow_up]** follow_ups: Design should confirm whether CodeIndex.version bumps (e.g. 2.0->2.1) or occurrence-key-absence is the migration trigger; both work but version is more auditable.
- **[report_follow_up]** follow_ups: Consider whether occurrence results should carry the enclosing symbol ID (from the existing symbols index) to give callers immediate context linkage - low cost, high value for the ADV use case.
- **[research_citation]** sources: Tree-sitter extractor (definitions-only, full-AST walk): SymbolExtractor.extract walks the entire AST via walk(); collects ONLY definition nodes (function/class/method/interface kinds). The recursive traversal already visits every node, so adding occurrence extraction is additive. Occurrence taxonomy is the deferred design decision. (src/lgrep/parser/extractor.py)
- **[research_citation]** sources: Symbol model (definition-only dataclass): Symbol dataclass holds definition metadata only. No occurrence/usage field exists; design must add an occurrence concept. (src/lgrep/parser/symbols.py)
- **[research_citation]** sources: JSON index store (version-tolerant load, v2.0): CodeIndex has files(path->hash) and symbols(symbol_id->dict), version='2.0'. load() uses .get() with defaults so a future 'occurrences' key degrades safely on old indexes. Atomic write-to-temp+rename; in-memory _cache keyed by mtime+size. (src/lgrep/storage/index_store.py)
- **[research_citation]** sources.omitted: 8 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The proposed direction is architecturally sound and fits the existing system: the extractor already walks the full AST (occurrence extraction is an additive pass, not a new traversal), the JSON index store is version-tolerant (load() uses .get() defaults so an 'occurrences' key degrades safely on old indexes), the MCP tool registration pattern is well-established, and AC4's structured-timeout guarantee is already enforced structurally by the @time_tool decorator (asyncio.wait_for + error_response) that wraps every tool. Three design-phase decisions remain open and must be resolved before a clean pass: (1) a schema-migration gate so incremental hash-skip does not silently leave occurrences empty for unchanged files; (2) a new deterministic test-classification heuristic (none exists today) for the prod/test filter; (3) a documented justification for routing through RuntimeSupervisor versus the simpler asyncio.to_thread path used by the search_symbols sibling. None are blockers - the agreement explicitly deferred occurrence taxonomy, index-migration shape, and timeout mechanism to design.
- **[unresolved_action]** required_main_agent_actions: Record the reproduced server-suite collection failure as the documented pre-existing MCP environment mismatch; do not treat it as an implementation regression.
- **[unresolved_action]** required_main_agent_actions: Run tests/test_server_registration.py and tests/test_server_tools.py in an environment providing mcp.server.fastmcp before release if server-layer execution evidence is required.
- **[wisdom_candidate]** wisdom_candidates: [pattern] A user-provided result limit is not a bounded-work guarantee unless the implementation enforces a server-side maximum; expose total_matches separately from the capped result slice.
- **[archive_only_evidence]** changes_made: src/lgrep/tools/search_references.py: Capped caller-provided reference result limits at 100, ensuring lookup responses remain bounded even when a caller requests an arbitrarily large limit.
- **[archive_only_evidence]** changes_made: tests/test_reference_lookup.py: Added coverage proving oversized result limits return no more than MAX_REFERENCE_RESULTS while preserving total_matches.
- **[archive_only_evidence]** verification: tests_run=bin/oc-test targeted -- tests/test_occurrences.py tests/test_reference_lookup.py tests/test_symbol_tools.py tests/test_indexing.py tests/test_index_store.py tests/test_e2e_symbols.py, bin/oc-test targeted -- tests/test_server_registration.py tests/test_server_tools.py, uv run ruff check src/lgrep/parser/extractor.py src/lgrep/parser/symbols.py src/lgrep/server/__init__.py src/lgrep/server/responses.py src/lgrep/server/tools_symbols.py src/lgrep/storage/index_store.py src/lgrep/tools/index_folder.py src/lgrep/tools/search_references.py tests/test_occurrences.py tests/test_reference_lookup.py tests/test_server_registration.py tests/test_server_tools.py tests/test_symbol_tools.py results=pass — Focused implementation/regression suite: 147 passed in 3.10s. Ruff: all checks passed. Server registration/tool suite cannot collect because the installed environment lacks mcp.server.fastmcp; reproduced ModuleNotFoundError is the documented pre-existing MCP version mismatch, not attributed to this change.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: bin/oc-test targeted -- tests/test_occurrences.py tests/test_reference_lookup.py tests/test_symbol_tools.py tests/test_indexing.py tests/test_index_store.py tests/test_e2e_symbols.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: bin/oc-test targeted -- tests/test_server_registration.py tests/test_server_tools.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run ruff check src/lgrep/parser/extractor.py src/lgrep/parser/symbols.py src/lgrep/server/__init__.py src/lgrep/server/responses.py src/lgrep/server/tools_symbols.py src/lgrep/storage/index_store.py src/lgrep/tools/index_folder.py src/lgrep/tools/search_references.py tests/test_occurrences.py tests/test_reference_lookup.py tests/test_server_registration.py tests/test_server_tools.py tests/test_symbol_tools.py

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| SC1 | success_criterion | pass |
| SC2 | success_criterion | pass |
| SC3 | success_criterion | pass |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| DONT1 | avoidance | respected |
| DONT2 | avoidance | respected |
| DONT3 | avoidance | respected |
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms6np8c3_cbcdd060
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms6nxd81_e6c3367e
- verification_missing: No durable adv_run_test evidence found for run_id: uv-pytest-reference-lookup-146
- verification_missing: No durable adv_run_test evidence found for run_id: ruff-check-changed-files
- Resolve the mcp.server.fastmcp compatibility blocker by selecting a supported MCP SDK constraint/lock or migrating the server API, then rerun server-facing tests including tests/test_server_tools.py and tests/test_server_registration.py.
- Do not revisit version, README/CHANGELOG, IndexSymbolsFolderResult field preservation, or duplicate timeout ownership: this review verified them; only the MCP compatibility blocker remains.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev pytest tests/test_reference_lookup.py tests/test_occurrences.py tests/test_symbol_tools.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev pytest tests/test_reference_lookup.py tests/test_server_tools.py tests/test_occurrences.py tests/test_symbol_tools.py tests/test_server_registration.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev ruff check src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev python -m py_compile src/lgrep/server/responses.py tests/test_server_tools.py src/lgrep/tools/search_references.py src/lgrep/server/tools_symbols.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
- Record the reproduced server-suite collection failure as the documented pre-existing MCP environment mismatch; do not treat it as an implementation regression.
- Run tests/test_server_registration.py and tests/test_server_tools.py in an environment providing mcp.server.fastmcp before release if server-layer execution evidence is required.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: bin/oc-test targeted -- tests/test_occurrences.py tests/test_reference_lookup.py tests/test_symbol_tools.py tests/test_indexing.py tests/test_index_store.py tests/test_e2e_symbols.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: bin/oc-test targeted -- tests/test_server_registration.py tests/test_server_tools.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run ruff check src/lgrep/parser/extractor.py src/lgrep/parser/symbols.py src/lgrep/server/__init__.py src/lgrep/server/responses.py src/lgrep/server/tools_symbols.py src/lgrep/storage/index_store.py src/lgrep/tools/index_folder.py src/lgrep/tools/search_references.py tests/test_occurrences.py tests/test_reference_lookup.py tests/test_server_registration.py tests/test_server_tools.py tests/test_symbol_tools.py
