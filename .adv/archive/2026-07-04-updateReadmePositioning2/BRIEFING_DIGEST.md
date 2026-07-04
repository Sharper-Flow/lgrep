# Archive Briefing Digest

**Change ID:** updateReadmePositioning2
**Title:** Update README positioning
**Status:** archived
**Generated:** 2026-07-04T02:54:46.898Z

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

Showing 15 of 15 durable facts.

- **[agenda]** follow_ups: Writer must re-verify the jCodeMunch comparison table rows against the live README at edit time; jCodeMunch positioning is actively evolving (semantic/hybrid now opt-in).
- **[agenda]** follow_ups: Consider a one-line note distinguishing lgrep_index_symbols_repo (local convenience GitHub symbol indexing) from searchcode's remote/public research lane to avoid reader confusion.
- **[archive_only_evidence]** sources: jCodeMunch current README (raw): Positions primarily as 'leading, most token-efficient MCP server for precise GitHub source code retrieval via tree-sitter AST'. Leads with token-savings telemetry (95%+ cut, 313B+ tokens saved, 45,000+ developers, $1.58M avoided). Broad client support (Claude Code, Cursor, VS Code, Codex, Continue, Windsurf). Now includes opt-in BM25/fuzzy/semantic/hybrid search, token-budgeted context assembly, call hierarchy, git-diff-to-symbol, PageRank centrality, live watch reindex, agent hooks. Commercial license required; non-commercial free. Indexes local folders OR GitHub repos.
- **[archive_only_evidence]** sources: lgrep current README: Already has strong local-first pitch, dual-engine (semantic Voyage Code3 + tree-sitter symbol), 'lgrep vs jCodeMunch' section + comparison table (lines 104-123), MIT licensing note. Exposes lgrep_index_symbols_repo(repo) for GitHub symbol indexing (line 372) — a partial remote overlap not framed against searchcode. No explicit searchcode complementarity section exists in README yet.
- **[archive_only_evidence]** sources: searchcode role: Analyzes/searches/retrieves code from any public git repo without prebuilt local index; adds language/complexity/tech-stack/static/security analysis. Complementary to lgrep's local lane.
- **[archive_only_evidence]** sources: Adjacent competitors (Nexus-MCP, other AST-graph MCPs, mgrep): Crowded 'fully-local hybrid search + code graph' space (Nexus-MCP: hybrid+graph+memory local; mgrep: semantic-only cloud with web search). Confirms lgrep should differentiate on OpenCode-first shared-warm-server + intent-first-then-drill workflow, not on feature-count.
- **[archive_only_evidence]** architecture_assessment: This is a docs-only positioning change and the approved agreement already captures source-backed discovery findings and AC1-AC6. The existing README already implements most of the intended positioning (local-first pitch, jCodeMunch comparison table, MIT note). Scout surfaced a small number of leverage points and one real risk: (1) jCodeMunch's live positioning has shifted to token-efficiency + GitHub-retrieval-first with heavy telemetry marketing — lgrep's comparison should anchor on the axis (intent-first local discovery for evolving repos vs symbol-first precise retrieval / token-packing) rather than a stale feature checklist, since jCodeMunch now also has opt-in semantic/hybrid search and would make a naive 'Semantic search: No' row inaccurate. (2) lgrep exposes lgrep_index_symbols_repo(repo) for GitHub symbol indexing, a partial remote overlap that must be reconciled with the 'searchcode owns remote/public lane' boundary to avoid an internal contradiction. (3) The crowded fully-local hybrid-search field means lgrep's durable differentiator is the OpenCode-oriented shared-warm-server + intent-first-then-structure flow, which should be the headline, not raw capability breadth. All candidates stay strictly within docs-only scope and respect avoidances (no feature creep, no unsupported benchmarks, no remote-search repositioning).
- **[agenda]** follow_ups: Execution: run a grep check for 'searchcode' and stale competitor rows on the final README diff to prove AC3/AC2/DONT2/DONT3 non-regression.
- **[archive_only_evidence]** sources: lgrep README.md current state (comparison section): Current README has a direct jCodeMunch comparison table. Line 113 asserts jCodeMunch 'Semantic search: No' — the stale claim the design targets. README already contains strong positive local-first positioning (lines 40-85) and no searchcode mention.
- **[archive_only_evidence]** sources: jCodeMunch official README (opt-in semantic/hybrid search): Official README lists 'semantic/hybrid search (opt-in, zero mandatory dependencies)' as a shipped feature; recommends `pip install jcodemunch-mcp[local-embed]` bundled ONNX encoder for zero-config semantic search.
- **[archive_only_evidence]** sources: jCodeMunch USER_GUIDE + SPEC (semantic tool params): search_symbols supports semantic/hybrid search via semantic=true, semantic_weight, semantic_only; BM25+embedding blend; embed_repo warm-up. Confirms lgrep README 'Semantic search: No' row for jCodeMunch is factually stale.
- **[archive_only_evidence]** sources: ADV change contract (SC1-SC4, AC1-AC6, C1-C5, DONT1-DONT5): Standard-rigor contract; user-approved decisions: remove direct jCodeMunch comparison, no searchcode mention, positive-lane-only positioning, no unsupported benchmark/adoption claims.
- **[archive_only_evidence]** architecture_assessment: Docs-only positioning change. Design chooses durable workflow-based positioning (local repos on disk, evolving code during agent work, intent-first semantic discovery then symbol/outline drill-down) over a fast-moving competitor feature table. The design's central factual premise — that jCodeMunch now ships opt-in semantic/hybrid search making the current README table row 'Semantic search: No' stale — is independently verified against jCodeMunch's official README, USER_GUIDE, and SPEC. Removing the direct comparison table (rather than patching one cell) is the correct maintenance-risk decision: competitor feature tables for actively developed tools structurally invite recurring staleness. Design maps cleanly to every contract item: SC1/AC1 (local-first positioning already partly present, to be strengthened), SC2/AC2 (intent-first then drill-down; removal/rewrite of jCodeMunch table), SC4/AC3 (no searchcode — already absent, design forbids adding), AC4 (claims bounded to implemented behavior: semantic search, symbol search, outlines, local LanceDB/JSON storage, shared warm server — all match README 'What lgrep is'), AC5/DONT5 (no unsupported numbers). Design explicitly preserves grep/rg and mgrep comparisons, which is consistent with the agreement scoping only jCodeMunch/searchcode. Positive-lane-only framing (DONT decision) is consistent with existing README tone. No spec deltas required; documentation-only, preserves lgrepToolSelectionOptimization expectations.
- **[archive_only_evidence]** changes_made: README.md: Replaced unsupported numeric resource-profile table with qualitative resource considerations, preserving local storage/shared-server behavior without numeric performance claims.
- **[archive_only_evidence]** verification: tests_run=test -e README.md && printf OK || printf MISSING, python - <<'PY'
from pathlib import Path
s=Path('README.md').read_text().lower()
required=[
('local-first positioning','local-first code intelligence' in s and 'active local development' in s),
('intent-first workflow','find the implementation by intent' in s and 'narrow to the right file or symbol' in s),
('implemented behavior semantic/symbol/outlines/local cache/shared server', all(term in s for term in ['semantic engine','symbol engine','outlines','local lancedb storage','shared warm process'])),
('no searchcode','searchcode' not in s),
('no jcodemunch','jcodemunch' not in s),
('no benchmark wording','benchmark' not in s),
('no removed resource numerics', all(term not in s for term in ['~300mb','~500mb','~250mb','<1%'])),
]
for name, ok in required:
    print(f'{name}: {"PASS" if ok else "FAIL"}')
raise SystemExit(0 if all(ok for _, ok in required) else 1)
PY results=pass — Path preflight returned OK. README static contract check passed all assertions after docs fix. git diff shows only README.md resource-profile wording changed. Initial requested scope_key acceptance-review was rejected by schema, so report used valid review:acceptance scope.

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| SC1 | success_criterion | pass |
| SC2 | success_criterion | pass |
| SC3 | success_criterion | pass |
| SC4 | success_criterion | pass |
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
| C5 | constraint | respected |
| DONT1 | avoidance | respected |
| DONT2 | avoidance | respected |
| DONT3 | avoidance | respected |
| DONT4 | avoidance | respected |
| DONT5 | avoidance | respected |

## Unresolved Actions

None
