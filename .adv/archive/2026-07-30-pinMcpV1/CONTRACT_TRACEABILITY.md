# Contract Traceability

**Change ID:** pinMcpV1
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-30T02:46:30.757Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | pyproject.toml declares mcp>=1.28,<2. Guard tr_ms6wh8g0_0ac58282 failed against the prior unbounded declaration and tr_ms6wvhpp_69ac3e60 passes after the pin. |
| AC2 | acceptance_criterion | pass | test | tests/test_server_registration.py asserts the exact 21-tool surface including search_references and now collects; it passes within the 698-test run tr_ms6wvhpp_69ac3e60. Reviewer independently confirmed it fully satisfies this criterion. |
| AC3 | acceptance_criterion | pass | test | test_mcp_dependency_excludes_unmigrated_major samples 2.0.0, 2.0.0rc1, 2.1.0, 2.99.0, 3.0.0 and 10.0.0. Verified it flags mcp>=1.0.0, mcp>=1.28, mcp>=1.28,<3 and bare mcp, while accepting mcp>=1.28,<2, mcp~=1.28 and mcp==1.29.0. |
| AC4 | acceptance_criterion | pass | test | tr_ms6wvhpp_69ac3e60: 698 passed, ruff clean. Previously uncollectable server and CLI suites now collect. Collection also exposed a real search_references defect, which was fixed. |
| AC5 | acceptance_criterion | pass | test | tr_ms6wms70_ded7b806: ephemeral no-cache build resolved mcp 1.29.0, initialized over stdio, advertised all 21 tools including search_references. Reviewer reproduced via a fresh wheel install. |
| AC6 | acceptance_criterion | pass | test | Version 3.2.1 in pyproject.toml and src/lgrep/__init__.py, enforced by test_version_matches_pyproject. CHANGELOG records both fixes and warns that v3.2.0 is unusable where mcp 2.x resolves. Release tag is created during archive finalization. |
| C1 | constraint | respected | static_check | No mcp v2 API migration: src/lgrep/server still imports FastMCP from mcp.server.fastmcp, and the diff touches only the dependency bound, the partial binding, tests, changelog and version. |
| C2 | constraint | respected | static_check | Tool semantics unchanged. The functools.partial change repairs an unconditional TypeError so the declared candidate-lookup contract can execute; it alters no inputs, filters, ordering, cap or disclaimer. |
| C3 | constraint | respected | static_check | No Vision configuration was modified and no service was restarted during this change. Verification used ephemeral uvx builds isolated from the shared tool installation. |
| C4 | constraint | respected | static_check | Both guards parse local project metadata and import the local server only. They perform no network resolution and are deterministic across the repeated runs recorded above. |
| OOS1 | out_of_scope | not_applicable | not_applicable | FastMCP to MCPServer migration was deliberately not attempted; it is tracked as separate follow-on work. |
| OOS2 | out_of_scope | not_applicable | not_applicable | No other dependency was upgraded. The only dependency addition is packaging in dev extras, required by the guard and confirmed appropriate by the reviewer. |
| OOS3 | out_of_scope | not_applicable | not_applicable | No lockfile was introduced and no broader dependency-bound policy was applied; other runtime requirements remain unbounded and are recorded as a follow-up. |
| OOS4 | out_of_scope | not_applicable | not_applicable | Vision redeployment and managed-service validation were not performed here; the parent change testVisionLgrepIntegration owns that scope. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-308bad231183 | AC1, AC2, AC3, AC4 |  | C1, C2, C3, C4, OOS1, OOS2, OOS3 |  |
| tk-402fd776b263 | AC5 |  | C2, C3, OOS4 |  |
| tk-9c4dd1615087 | AC6 |  | C1, OOS1, OOS2 |  |
