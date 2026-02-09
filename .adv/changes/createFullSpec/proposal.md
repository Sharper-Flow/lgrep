# Change: create full spec

## Why

The ADV workspace currently has one capability spec with limited coverage for end-to-end spec-driven workflows. This change defines a complete, implementable spec set so future work can be validated against clear laws, reduce ambiguity during implementation, and improve consistency of quality gates.

## What Changes

- Expand the existing capability definition to cover full lifecycle behavior (proposal, prep, validate, apply, review, harden, archive).
- Add explicit, testable requirements for proposal quality, task orchestration, and gate progression.
- Define acceptance scenarios for happy path and key failure cases (missing inputs, invalid sequencing, conflicting changes).
- Document measurable operational expectations (status markers, artifacts produced, and validation outcomes).

## Success Criteria

Each criterion below is specific, measurable, and independently verifiable.

1. [ ] A full capability spec exists with requirements for all ADV lifecycle stages and passes `adv_change_validate` without conflicts.
2. [ ] Proposal quality requirements enforce hard checks for unambiguous wording, verifiable acceptance criteria, and traceability, while keeping full INVEST as advisory guidance.
3. [ ] Task and gate sequencing is defined as an explicit finite-state transition model with mandatory and conditional gates, and invalid transitions return actionable errors.
4. [ ] Acceptance coverage is defined as a compact 3-scenario matrix (success, validation failure, remediation) with deterministic fixtures and observable outcomes.
5. [ ] The spec documentation includes command-level examples for `/adv-prep`, `/adv-validate`, and `/adv-apply`, each with expected inputs and observable success/failure outcomes.

## Research Validation

### Summary

Architecture research validated the core direction while identifying simplification opportunities. The proposal now favors a boring, maintainable model: minimal hard requirement-quality checks, explicit gate-state transitions, and a compact acceptance scenario matrix.

### Validated Decisions

- Keep measurable, testable requirements as mandatory policy.
- Keep command-level lifecycle examples for `/adv-prep`, `/adv-validate`, and `/adv-apply`.
- Keep explicit failure/remediation scenarios as acceptance criteria.

### Simplification Opportunities

| Current | Simpler Alternative | Effort | Recommendation |
|---------|---------------------|--------|----------------|
| Hard enforcement of full INVEST | Hard-enforce only unambiguous + verifiable + traceable checks; keep INVEST advisory | low | adopt simpler mandatory checks |
| Strict one-path 6-gate linear flow | Explicit FSM with mandatory + conditional gates and guard-based transitions | medium | implement FSM-style gate law |
| Broad acceptance-test expectations | Compact 3-scenario matrix + command contract tests | low | keep matrix small and deterministic |

### Concerns

- Overly rigid fixed gate sequences can create process deadlocks and unnecessary friction.
- Smell detection used as hard reject can create false positives; needs documented override path.
- End-to-end-only acceptance testing risks flaky, slow feedback and brittle tests.

### Anti-Patterns Detected

- Over-constraining estimation/process detail where it does not improve quality outcomes.
- Treating lint-style requirement smells as absolute policy failures without reviewer override.

### Over-Engineering Flags

- Full hard-scoring of all INVEST dimensions for every requirement.
- Requiring the full 6-gate sequence unconditionally for all change risk levels.

### Action Items

- [ ] Define mandatory requirement checks (unambiguous, verifiable, traceable) and mark INVEST as advisory.
- [ ] Define gate transitions as explicit FSM with mandatory/conditional gates and clear guard error messages.
- [ ] Define acceptance strategy as command contract tests plus a compact 3-scenario integration matrix.

### Sources

- https://xp123.com/articles/invest-in-good-stories-and-smart-tasks/
- https://xp123.com/articles/estimable-stories-in-the-invest-model/
- https://www.nasa.gov/reference/appendix-c-how-to-write-a-good-requirement/
- https://cucumber.io/docs/gherkin/reference/
- https://martinfowler.com/articles/practical-test-pyramid.html
- https://martinfowler.com/bliki/DeploymentPipeline.html
- https://learn.microsoft.com/en-us/azure/devops/pipelines/process/approvals?view=azure-devops
- https://csrc.nist.gov/pubs/sp/800/218/final

## Affected Code

- `.adv/specs/lgrepToolSelectionOptimization/spec.json`
- `.adv/changes/createFullSpec/change.json`
- `.adv/changes/createFullSpec/proposal.md`

## Constraints

- MUST: Keep requirements implementation-agnostic and focused on behavior/outcomes.
- MUST NOT: Use subjective terms (e.g., "user-friendly", "fast", "robust") without measurable thresholds.
- SHOULD: Reuse existing ADV conventions for gate names, task states, and status markers.

## Cross-Cutting Concerns Coverage

- Error handling: In scope. Invalid transition and missing-input paths must return structured remediation messages.
- Logging/observability: In scope. Prep/validate/apply flows must emit status markers and auditable artifacts.
- Validation: In scope. Proposal quality checks and transition guards are mandatory.
- Security/privacy: In scope for governance safety. Override transitions must record approver identity, reason, and timestamp; no new PII collection is introduced.
- Performance: In scope. Acceptance matrix remains deterministic and compact (3 scenarios) to keep feedback stable.
- Config: In scope. Command examples must document required inputs and expected outcomes.
- Concurrency: N/A for this change set. This proposal defines laws/contract behavior, not concurrent execution primitives.
- Persistence: N/A for new data models. Existing ADV storage model is reused; only requirement coverage expands.
- Caching: N/A. No cache behavior changes are proposed in this spec expansion.
- i18n/l10n: N/A. Lifecycle contracts are internal engineering workflow requirements.

## Cross-Spec Consistency Note

- Current repository has one deployed capability (`lgrepToolSelectionOptimization`) focused on tool selection behavior.
- This change introduces ADV lifecycle requirements, which initially indicated a capability scope mismatch.
- Decision: extend `lgrepToolSelectionOptimization` in this change to keep migration simple and avoid introducing a parallel capability namespace mid-stream.
- Follow-up: if lifecycle scope continues to grow beyond lgrep/tool-selection concerns, split to a dedicated capability in a subsequent change.

## Impact

- Affected specs: `lgrepToolSelectionOptimization`
- Breaking changes: no
- Dependencies: none

## Context

- Brainstorm: none found in `./temp/brainstorm-*.md`
