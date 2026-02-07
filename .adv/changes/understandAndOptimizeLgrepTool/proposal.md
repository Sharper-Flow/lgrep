# Change: Understand and optimize lgrep tool usage in OpenCode so agents reliably prefer lgrep over grep/read workflows

## Why

Agents currently have multiple overlapping search options (`lgrep`, `grep`, `glob`, direct file reads). In practice, they may bypass `lgrep` even when semantic search is the better first step. This creates inconsistent behavior, lower retrieval quality, and extra token/time cost from trial-and-error search patterns.

We need a complete, measurable optimization of the lgrep-to-OpenCode integration so lgrep becomes the default discovery path when appropriate, while preserving exact-match and direct-read workflows where they are still the right tool.

## What Changes

- Audit and document the current lgrep tool path in OpenCode (MCP server setup, skill guidance, and runtime invocation path).
- Define explicit tool-selection policy for agents: when to use `lgrep`, when `grep` is preferred, and when direct reads are preferred.
- Implement integration improvements so agent behavior aligns with the policy (including setup/onboarding ergonomics and prompts/skill instructions).
- Add observable verification for tool-choice outcomes (tests and measurable checks) to prove lgrep is selected in intended scenarios.
- Update user/operator documentation with a clear recommended setup and migration notes.

## Success Criteria

1. [ ] For intent-based code search prompts, automated tests verify lgrep is chosen as first search action in at least 90% of covered scenarios.
2. [ ] For exact identifier/regex prompts, automated tests verify non-semantic tools (`grep`-style exact search) are chosen in at least 90% of covered scenarios.
3. [ ] In a fresh OpenCode project setup with no existing lgrep cache, the first semantic search workflow succeeds without any manual `lgrep_index` invocation from the user (validated by integration test).
4. [ ] Documentation includes an explicit decision matrix for tool choice and a validated setup path; at least one integration test follows this documented path end-to-end.
5. [ ] No regression in existing lgrep capabilities: all pre-existing lgrep server tests pass.

## Affected Code

- `src/lgrep/server.py`
- `src/lgrep/cli.py`
- `skills/lgrep/SKILL.md`
- `README.md`
- `examples/opencode.json`
- `tests/test_server.py`
- `tests/test_integration.py`

## Constraints

- MUST: Preserve compatibility with existing lgrep MCP tool names and parameters.
- MUST: Keep behavior safe for multi-project usage where project path is explicit.
- MUST: Provide objective pass/fail checks for tool-selection behavior.
- MUST NOT: Force semantic search for exact-match/refactor workflows where exact tools are more appropriate.
- MUST NOT: Introduce changes that require Vision-specific behavior to function.
- SHOULD: Minimize user setup friction and cold-start overhead.
- SHOULD: Keep implementation incremental and test-first.

## Impact

- Affected specs: New capability (tool-selection and auto-setup behavior for lgrep in OpenCode).
- Breaking changes: No.
- Dependencies: None required by default; prefer existing stack and interfaces.

## Context

- No brainstorm document was found under `temp/brainstorm-*.md` at proposal time.

## Research Validation

### Summary

Research confirms the root issue is primarily **tool-selection policy**, not MCP itself. The simplest robust architecture is to keep lgrep as MCP tools, reduce competing tool ambiguity via agent/skill policy, and keep transport concerns separate from tool-choice behavior.

### Validated Decisions

- Keep lgrep as native MCP tools (no wrapper-first redesign required).
- Make tool-choice explicit in skill/agent policy: semantic discovery uses lgrep; exact identifier/regex uses grep.
- Preserve explicit project path scoping for safety and multi-project correctness.

### Simplification Opportunities

| Current | Simpler Alternative | Effort | Recommendation |
|---------|---------------------|--------|----------------|
| Treat MCP registration as enough to drive usage | Keep MCP transport, add explicit decision matrix + scoped tool availability in prompts/skills | Low | Adopt immediately |
| Local streamable-http treated as default candidate | Default to stdio for local OpenCode, document streamable-http as opt-in shared mode | Low | Adopt as default |
| Manual first index requirement | Auto-index on first semantic search (with safeguards), keep manual index as prewarm option | Medium | Implement in this change |

### Concerns

- Overlapping search tools increase mis-selection risk and token overhead.
- Local HTTP transport adds security/ops burden if used by default (origin/auth/binding requirements).
- Background watcher-only onboarding can create hidden cost/work; should remain opt-in.

### Anti-Patterns Detected

- Assuming "tool is registered" implies "tool will be selected first".
- Expanding integration with wrapper layers before exhausting native policy/scoping controls.

### Over-Engineering Flags

- Wrapper-first routing to force lgrep selection is likely unnecessary if OpenCode tool scoping + skill policy is configured correctly.
- Always-on daemon/proxy setups for single-user local workflows add complexity without proportional benefit.

### Detailed Findings

#### Tool-selection policy
- **Current:** MCP-first expectation without strict policy has led to inconsistent lgrep usage.
- **Research:** MCP defines tool exposure/calling, but selection remains model/prompt policy; OpenCode supports tool scoping and guidance that should be used directly.
- **Simpler option:** Keep current MCP integration and improve policy/instructions rather than adding wrapper architecture.
- **Recommendation:** Encode a clear decision matrix in `skills/lgrep/SKILL.md` and align agent/tool scope.
- **Sources:**
  - https://modelcontextprotocol.io/specification/2025-06-18/server/tools
  - https://opencode.ai/docs/mcp-servers/
  - https://opencode.ai/docs/agents/
  - https://platform.openai.com/docs/guides/function-calling
  - https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview

#### Transport defaults
- **Current:** both stdio and streamable-http are supported and under evaluation.
- **Research:** stdio is the boring default for local process-spawned workflows; streamable-http is better for shared/remote/service scenarios.
- **Simpler option:** default stdio; gate HTTP behind explicit shared-mode requirements.
- **Recommendation:** Document transport decision tree and security controls for HTTP mode.
- **Sources:**
  - https://modelcontextprotocol.io/specification/draft/basic/transports
  - https://modelcontextprotocol.io/specification/draft/basic/security_best_practices
  - https://github.com/modelcontextprotocol/python-sdk/blob/main/README.v2.md

#### First-run indexing UX
- **Current:** manual first index is required (`lgrep_index`).
- **Research:** modern tools favor auto indexing/first-use sync for adoption; manual-only onboarding is a friction point.
- **Simpler option:** auto-index on first semantic query for explicit path, with status + lock + retry safeguards.
- **Recommendation:** implement auto-index-on-first-search as default path while keeping manual prewarm and optional watcher.
- **Sources:**
  - https://cursor.com/docs/context/semantic-search
  - https://docs.github.com/en/enterprise-cloud@latest/copilot/concepts/context/repository-indexing
  - https://sourcegraph.com/blog/announcing-auto-indexing

### Action Items

- [ ] Add and enforce a tool-choice decision matrix in lgrep skill docs and integration docs.
- [ ] Make stdio the documented local default; define streamable-http opt-in criteria and localhost security checklist.
- [ ] Add auto-index-on-first-search workflow with single-flight locks, retry/backoff, and user-visible status states.

## Prep Gap Analysis Additions

### Requirements Clarifications (INVEST/Testability)

- Success criteria #1 and #2 now require a fixed scenario matrix (stable denominator) for the 90% threshold.
- First-search onboarding now explicitly includes negative-path scenarios (missing credentials, indexing failure) and concurrency behavior.
- Transport docs now explicitly require a non-default streamable-http posture with security controls documented.

### Cross-Cutting Concerns Coverage

- **Error Handling:** add explicit negative-path tests for first-search onboarding failures.
- **Logging/Monitoring:** add checks for structured status events across auto-index start/success/retry/failure.
- **Validation:** ensure path and credential failure responses are actionable and deterministic.
- **Security:** document streamable-http opt-in controls (localhost binding, auth expectations, origin/CORS guidance).
- **Performance:** add a cold-start guardrail and warm-path non-regression check.
- **Concurrency:** add single-flight initialization test coverage.
- **Config:** document transport decision tree and expected defaults in examples.
- **Caching:** verify first-run index creation and warm cache behavior are both covered in tests.

### Cross-Cutting Concerns Marked N/A (with rationale)

- **Persistence model redesign:** N/A for this change; existing LanceDB persistence remains authoritative.
- **i18n/L10n:** N/A; changes are infrastructure/tooling behavior, not user-facing localized UI strings.
- **Privacy/GDPR expansion:** N/A beyond existing local-only storage model; no new data classes introduced.

### Cross-Spec Consistency

- No deployed specs currently exist in this repository (`adv_spec_list` returned empty), so no inter-spec conflicts were detected.

### Confidence

- **High:** policy-driven tool-selection guidance, transport defaults for local vs shared deployments.
- **Medium:** exact UX/performance thresholds for first-query indexing in very large repos.
