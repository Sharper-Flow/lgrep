# Contract Traceability

**Change ID:** testVisionLgrepIntegration
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-30T05:23:58.460Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| AC1 | acceptance_criterion | pass | test | Protocol evidence tr_ms6xhvom_e02ee32d: the deployed Vision-managed binary initializes over real MCP and advertises all 21 tools including search_references. Re-confirmed today against the currently deployed artifact (v3.2.2) via tr_ms72gdpd_770ff8e1, check 'managed endpoint exposes search_references' = pass. |
| AC2 | acceptance_criterion | pass | test | Verified today against the deployed managed endpoint on port 6278 across two runs. tr_ms72gdpd_770ff8e1: 'include_tests returns test rows' pass, 'three filters are distinct' pass (production_first 100 / include_tests 100 / tests_only 5), 'production_first unchanged (no test rows)' pass. tr_ms72higs_c69e6e03: 'candidate disclaimer present' pass ('Candidate occurrences only; results are not compiler-accurate or exhaustive.'), 'result cap enforced at 100 (limit=100)' pass (100 returned of 142 total matches), 'limit above MAX_REFERENCE_RESULTS is clamped to 100' pass (requested 500, returned 100). All five required behaviors observed. |
| AC3 | acceptance_criterion | pass | test | Runs tr_ms6xq8ef_ccb76250 and tr_ms6xqzks_5095cd1f: empty query, invalid usage_filter, invalid kind, and unindexed path all return bounded schema-stable error fields; a client abort mid-request leaves the service healthy; slowest managed request 0.038s against the 8s budget. Re-confirmed today: tr_ms72gdpd_770ff8e1 'all managed requests within 8s' pass at max 0.034s. |
| AC4 | acceptance_criterion | pass | test | Five genuinely simultaneous managed lookups completed in 0.162s wall time with LGREP_WORKER_MAX_THREADS=2, zero active jobs afterwards, and no abandonment. Diagnostics showed owned runtime jobs and exposed no secrets. |
| AC5 | acceptance_criterion | pass | test | Rollback was exercised for real, not merely documented. The first cutover shipped v3.2.0, failed AC1 because mcp 2.0.0 removed mcp.server.fastmcp, and was rolled back to the prior artifact with Vision restarted and verified healthy. Redeploy then installed the immutable tag v3.2.1, which resolved mcp 1.29.0 with no manual constraint. `vision config validate` passed, only the lgrep server was restarted, and Vision reported healthy with 12 servers running and 0 errors. |
| AC6 | acceptance_criterion | pass | test | Five durable findings recorded as promoted project wisdom, each carrying reproduction evidence, severity, impact, and disposition: ws-McJr7j (high, transport guard defeated by the Vision proxy, fix now), ws-cUmcli (high, include_tests indistinguishable from production_first at the cap, follow-up), ws-JLUOx1 (medium, stale rows served with no freshness signal, follow-up), ws-pIosyJ (pattern, classify managed MCP failures as client-catalog vs service-registration), ws-hS3NeI (failure, an unbounded dependency hid an uncollectable suite which hid a shipped feature that never executed, closed by pinMcpV1). |
| C1 | constraint | respected | static_check | Deployment used the existing uv-tool installation against an immutable release tag. `lgrep install-opencode`, which targets a different port, was not invoked at any point. |
| C2 | constraint | respected | static_check | `vision config validate` returned 'Configuration is valid' before lifecycle actions, and an explicit lgrep server restart was issued after binary replacement rather than relying on config reload alone. |
| C3 | constraint | respected | static_check | Validation used isolated temporary Python fixtures under a temp directory. No real repository index was modified. During the change's own execution the destructive guard finding was established by reading the transport diagnostic, explicitly recorded as 'Not proven by executing deletion, deliberately'. |
| C4 | constraint | respected | static_check | No edit was made to ~/.config/vision/servers.yaml. Environment values, session_timeout 30m, max_sessions 20, and LGREP_WORKER_MAX_THREADS=2 are unchanged; the AC4 run observed worker_max_threads 2 in effect. |
| OOS1 | out_of_scope | not_applicable | not_applicable | No finding was implemented in this change. All three tasks report empty touched_files, and the change carries no spec deltas. Remediation was routed to a separate approved change (fixLookupPruneDefects). |
| OOS2 | out_of_scope | not_applicable | not_applicable | Vision topology, ports, and global config policy were not altered. Only the lgrep server process was restarted, on its existing port 6278. |
| OOS3 | out_of_scope | not_applicable | not_applicable | Candidate lookup semantics were not changed by this change. The later semantic change to include_tests was made under the separately approved fixLookupPruneDefects, honoring this boundary. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-b04decce1e63 | AC1, AC5 |  | C1, C2, C4, OOS2 |  |
| tk-9b1311399dcc | AC2, AC3, AC4 |  | C2, C3, C4, OOS3 |  |
| tk-5310e12cf868 | AC6 |  | OOS1, OOS2, OOS3 |  |
