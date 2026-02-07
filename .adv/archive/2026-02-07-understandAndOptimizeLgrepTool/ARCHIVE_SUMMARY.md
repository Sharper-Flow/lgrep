# Archive: Understand and optimize lgrep tool usage in OpenCode so agents reliably prefer lgrep over grep/read workflows

**Change ID:** understandAndOptimizeLgrepTool
**Archived:** 2026-02-07T21:47:49.070Z
**Created:** 2026-02-07T19:03:04.530Z

## Tasks Completed

- ✅ Define and document tool-selection requirements for lgrep vs grep/read in OpenCode
- ✅ Update setup and usage documentation with decision matrix and verified workflow
- ⏭️ Update setup and usage documentation with decision matrix and verified workflow
- ✅ Write acceptance tests for agent tool-choice behavior and auto-setup workflow
- ⏭️ Write acceptance tests for agent tool-choice behavior and auto-setup workflow
- ✅ Implement integration updates to make lgrep the default semantic search path in intended scenarios
- ✅ Apply research: make MCP transport a deployment detail and encode tool-choice policy in skill/agent instructions (lgrep default for semantic discovery, grep for exact match)
- ✅ Apply research: set stdio as default transport for local OpenCode usage and document streamable-http as opt-in for shared/multi-client deployments with localhost security controls
- ✅ Apply research: implement auto-index-on-first-semantic-search as default onboarding path with explicit path scoping, single-flight lock, bounded retries, and clear status states
- ✅ Define an explicit scenario matrix for tool-choice acceptance tests (semantic intent vs exact identifier/regex) with a fixed denominator and pass threshold calculation so the 90% success criteria are objectively testable.
- ✅ Add negative-path integration tests for first-search onboarding: missing VOYAGE_API_KEY, indexing failure propagation, and user-facing remediation message without requiring manual lgrep_index.
- ✅ Add concurrency tests for auto-index-on-first-search single-flight behavior: concurrent searches for the same cold project should trigger one index initialization and produce consistent status outcomes.
- ✅ Document and verify streamable-http opt-in security controls (localhost binding, auth expectation, origin/CORS guidance, and explicit non-default stance) in README and examples/opencode.json.
- ✅ Add observability checks for the new onboarding path: structured events/status states for auto-index start, success, retry, and failure to make tool-choice behavior debuggable in CI logs.
- ✅ Define a cold-start performance guardrail for first semantic search (documented expectation and regression test/harness) and verify warm-path behavior remains unchanged.

## Specs Modified

- **lgrepToolSelectionOptimization**: 4 delta(s)
