# Acceptance

Reviewed at: 2026-07-29T23:41:10.395Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| SC1 | success_criterion | Agents can obtain local candidate-usage evidence for a named Python symbol before editing or review. | pass | Acceptance review confirmed local candidate lookup. |
| SC2 | success_criterion | Results support a production-first workflow with explicit test inclusion controls. | pass | Review confirmed production-first and test filters. |
| SC3 | success_criterion | Results stay local, bounded, and clearly limited rather than claiming accuracy they do not have. | pass | Review confirmed bounded local candidate semantics. |
| AC1 | acceptance_criterion | For an indexed Python repository, a caller can retrieve bounded candidate-usage locations and enclosing context for a requested symbol name. | pass | tr_ms6qa1sz_ba1aa8da: 147 passed. |
| AC2 | acceptance_criterion | When names are ambiguous, results visibly identify candidates and do not claim compiler-accurate or exhaustive resolution. | pass | tr_ms6qa1sz_ba1aa8da: ambiguity tests passed. |
| AC3 | acceptance_criterion | Callers can select production-first, include-tests, or tests-only result sets. | pass | tr_ms6qa1sz_ba1aa8da: filter tests passed. |
| AC4 | acceptance_criterion | Unavailable, stale, invalid, cancelled, or timed-out lookup states return a structured safe outcome without unbounded work or daemon degradation. | pass | tr_ms6qa1sz_ba1aa8da: structured error and limit tests passed. |
| AC5 | acceptance_criterion | Index refresh and worktree/cache handling preserve existing lifecycle and safety guarantees. | pass | tr_ms6q9s0p_cc4168df: 153 index lifecycle tests passed. |
| C1 | constraint | Initial language coverage is Python only. | respected | Python-only implementation and tests reviewed. |
| C2 | constraint | Repository code remains local. | respected | No external code service added. |
| C3 | constraint | Existing path, cache, worktree, timeout, cancellation, and daemon protections remain intact. | respected | Existing timeout/runtime paths reviewed. |
| DONT1 | avoidance | Do not claim exhaustive or compiler-accurate references. | respected | Candidate disclaimer reviewed. |
| DONT2 | avoidance | Do not add a full call graph, blast-radius model, LSP integration, remote indexing, or automatic modification. | respected | No graph, LSP, remote indexing, or modification added. |
| DONT3 | avoidance | Do not introduce unbounded background work. | respected | 100-result cap and existing bounded runtime retained. |
| OOS1 | out_of_scope | Non-Python language support. | not_applicable | Non-Python support excluded. |
| OOS2 | out_of_scope | Transitive impact analysis and graph-ranked retrieval. | not_applicable | Graph and transitive impact excluded. |

