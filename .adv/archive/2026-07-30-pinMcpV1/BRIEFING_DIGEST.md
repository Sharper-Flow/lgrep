# Archive Briefing Digest

**Change ID:** pinMcpV1
**Title:** Pin mcp to v1
**Status:** archived
**Generated:** 2026-07-30T03:01:06.098Z

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

Showing 10 of 10 durable facts.

- **[unresolved_action]** required_main_agent_actions: Create the required task checkpoint for the uncommitted reviewer remediation in tests/test_deps.py.
- **[unresolved_action]** required_main_agent_actions: Leave unrelated deprecation warnings and all Vision configuration/service state unchanged.
- **[wisdom_candidate]** wisdom_candidates: [pattern] Dependency compatibility guards should assert the structural requirement (for this case, a strict upper bound below the incompatible major), then use representative versions as supplementary evidence; sample-only checks can miss unusual widened specifiers.
- **[archive_only_evidence]** changes_made: tests/test_deps.py: Strengthened the MCP bound regression guard to require an explicit strict upper bound at or below 2, preventing sampled-version blind spots while retaining the supported-v1 assertion.
- **[archive_only_evidence]** verification: tests_run=uv lock --check, uv tree --package mcp, uv run --extra dev python -m pytest -q, uv run --extra dev ruff check src tests, uv build --wheel --out-dir /tmp/opencode/pinMcpV1-review.*; fresh venv install; import registration assertion results=pass — Lock is current and resolves mcp v1.29.0. Full suite: 698 passed in 20.32s (16 pre-existing deprecation warnings). Ruff: all checks passed. Freshly built wheel installed into a new temporary venv resolved mcp=1.29.0 and registered exactly 21 expected tools, including search_references.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv lock --check
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv tree --package mcp
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev python -m pytest -q
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev ruff check src tests
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv build --wheel --out-dir /tmp/opencode/pinMcpV1-review.*; fresh venv install; import registration assertion

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| AC6 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| C4 | constraint | respected |
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |
| OOS3 | out_of_scope | not_applicable |
| OOS4 | out_of_scope | not_applicable |

## Unresolved Actions

- Create the required task checkpoint for the uncommitted reviewer remediation in tests/test_deps.py.
- Leave unrelated deprecation warnings and all Vision configuration/service state unchanged.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv lock --check
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv tree --package mcp
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev python -m pytest -q
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv run --extra dev ruff check src tests
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: uv build --wheel --out-dir /tmp/opencode/pinMcpV1-review.*; fresh venv install; import registration assertion
