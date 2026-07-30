# Contract Traceability

**Change ID:** fixLookupPruneDefects
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-30T04:13:41.946Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | tests/test_maintenance_grant.py — grant absent coerces dry_run=True with refused_reason on stdio for both prune_orphans and prune_symbols; _destructive_grant_present() reads only LGREP_ALLOW_DESTRUCTIVE_MCP, transport excluded (src/lgrep/server/tools_maintenance.py). Wire-level: reverify.py scenario over real stdio MCP, run tr_ms6zwqdr_9adcc1e2. |
| AC2 | acceptance_criterion | pass | test | tests/test_maintenance_grant.py — with LGREP_ALLOW_DESTRUCTIVE_MCP set, dry_run=False is honoured and deletion occurs, refused_reason absent. CLI --execute path untouched; existing CLI prune tests unchanged and green in the 719-test suite (tr_ms6zwalc_0f197251). |
| AC3 | acceptance_criterion | pass | test | tests/test_reference_filters.py — over a corpus whose test occurrences fall beyond the cap, production_only / include_tests / tests_only return three distinct result sets and include_tests returns actual test occurrences via the 20% reserved slice (TEST_RESERVE_RATIO, src/lgrep/tools/search_references.py). |
| AC4 | acceptance_criterion | pass | test | tests/test_reference_filters.py asserts production_matches, test_matches, returned_production, returned_tests on truncated and untruncated responses; invariant returned_production + returned_tests == len(results) holds on every path (src/lgrep/server/responses.py). |
| AC5 | acceptance_criterion | pass | test | tests/test_reference_staleness.py (7 passed, tr_ms6zt69s_389f162b) — edited backing file marks is_stale and increments stale_file_count; re-indexing clears; deleted file reports stale rather than raising. Comparison uses the per-file SHA-256 the index already stores. |
| AC6 | acceptance_criterion | pass | test | Candidate-only framing unchanged in response contract and tool descriptions. Latency: max 0.023s across all ten protocol scenarios against the 8s per-request budget (tr_ms6zwqdr_9adcc1e2). |
| AC7 | acceptance_criterion | pass | test | Full suite 719 passed, ruff clean (tr_ms6zwalc_0f197251). All ten scenario checks pass over real JSON-RPC initialize -> tools/call against a clean ephemeral uvx build of the change branch (tr_ms6zwqdr_9adcc1e2, ALL_PASS: True). |
| C1 | constraint | respected | static_check | git diff main...HEAD touches no dependency manifest constraints: pyproject.toml diff is the version bump 3.2.1 -> 3.2.2 only; no lockfile or MCP SDK change. |
| C2 | constraint | respected | static_check | No file under ~/.config/vision/ modified; diff is confined to the lgrep repository. Vision topology and ports untouched. |
| C3 | constraint | respected | static_check | _annotate_staleness iterates only the returned rows (src/lgrep/tools/search_references.py), never the index or repository tree; cost is bounded by the result cap, plus a MAX_FILE_SIZE_BYTES read cap and repository-root path confinement added during review remediation. |
| C4 | constraint | respected | static_check | All new response fields are additive: is_stale, production_matches, test_matches, returned_production, returned_tests, stale_file_count, and refused_reason (NotRequired). No existing field removed, renamed, or retyped in src/lgrep/server/responses.py. |
| OOS1 | out_of_scope | not_applicable | not_applicable | No automatic re-indexing added. Review caught and fixed a violation of this boundary: _annotate_staleness was mutating the cached index rows in place, which would have persisted lookup verdicts into indexed state on the next save. Now returns copies (commit da2777f). |
| OOS2 | out_of_scope | not_applicable | not_applicable | No authentication mechanism added for the Vision proxy. The grant is a server-side capability flag, not a caller-identity scheme. |
| OOS3 | out_of_scope | not_applicable | not_applicable | Reference lookup remains Python-only and candidate-only; no language extension or semantic-resolution work in the diff. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-c149dd1461a8 | AC1, AC2 |  | C1, C2, OOS2 |  |
| tk-593a5819ec49 | AC3, AC4 |  | C4, OOS3 |  |
| tk-ec6f446f6a1d | AC5, AC6 |  | C3, C4, OOS1, OOS3 |  |
| tk-d93f0f72a3b7 | AC7 |  | C1, C2, OOS2 |  |
