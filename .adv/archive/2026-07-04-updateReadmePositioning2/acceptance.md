# Acceptance

Reviewed at: 2026-07-04T02:42:42.129Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| SC1 | success_criterion | README clearly positions lgrep as local-first code intelligence for repos already on disk and changing during active agent work. | pass | README lines 4, 29-31, 40-61 position lgrep as local-first code intelligence for repos on disk and changing during active agent work; reviewer verdict READY. |
| SC2 | success_criterion | README emphasizes intent-first discovery followed by symbol/outline drill-down. | pass | README lines 106-113 describe best-fit workflow: ask by meaning, inspect files/outlines, retrieve exact symbol/text. |
| SC3 | success_criterion | README no longer relies on direct jCodeMunch comparison claims that can go stale. | pass | Direct jCodeMunch comparison section/table removed; final static check tr_mr5raten_4f21d731 found no jCodeMunch/jcodemunch references in README. |
| SC4 | success_criterion | README avoids naming searchcode or other complementary tools; it states what lgrep is for positively. | pass | README states positive lgrep lane; final static check tr_mr5raten_4f21d731 found no searchcode references. |
| AC1 | acceptance_criterion | README intro or "What lgrep is" section includes a concise local-first/evolving-code positioning statement. | pass | adv_run_test tr_mr5raten_4f21d731 asserted required local-first/evolving-code phrases are present. |
| AC2 | acceptance_criterion | README comparison section removes direct jCodeMunch comparison or rewrites it so no stale feature-presence claim remains. | pass | adv_run_test tr_mr5raten_4f21d731 asserted jCodeMunch/jcodemunch absent; direct comparison removed from README. |
| AC3 | acceptance_criterion | README does not mention searchcode or position lgrep by contrast with searchcode. | pass | adv_run_test tr_mr5raten_4f21d731 asserted searchcode absent from README. |
| AC4 | acceptance_criterion | README keeps claims limited to implemented lgrep behavior: semantic search, symbol search, outlines, local cache/storage, shared warm server. | pass | adv_run_test tr_mr5raten_4f21d731 asserted semantic engine, symbol engine, local LanceDB storage, local JSON index, outlines/workflow phrases present; reviewer confirmed implemented-behavior claims only. |
| AC5 | acceptance_criterion | README adds no unsupported numeric benchmark, adoption, or performance claim for lgrep. | pass | adv_run_test tr_mr5raten_4f21d731 asserted forbidden unsupported numeric/adoption/benchmark terms absent; reviewer removed resource numerics. |
| AC6 | acceptance_criterion | Documentation-only verification passes via a README-focused review/check. | pass | adv_run_test tr_mr5raten_4f21d731 passed README-focused static check and git diff --check; reviewer verdict READY. |
| C1 | constraint | Keep README concise and product-facing. | respected | README diff is concise and product-facing; reviewer READY with no blockers. |
| C2 | constraint | Do not add new product promises for unimplemented features. | respected | README claims limited to implemented behavior verified by adv_run_test tr_mr5raten_4f21d731 and reviewer READY. |
| C3 | constraint | Do not change code unless a documentation link/build issue requires it. | respected | Only README.md touched in checkpoint commits 035987a and 7b9ad3a; no code files changed. |
| C4 | constraint | Preserve lgrep's OSS/local-first positioning. | respected | README headline and intro preserve OSS/local-first positioning: Local-first code intelligence, local repos, local storage. |
| C5 | constraint | Scope is current repo only. | respected | Only current repo README.md changed in ADV worktree. |
| DONT1 | avoidance | No feature creep into graph, call hierarchy, impact analysis, or remote public repo implementation. | respected | No graph/call hierarchy/impact analysis or remote public repo implementation added; docs-only README change. |
| DONT2 | avoidance | No direct public README positioning against searchcode. | respected | No searchcode mention; adv_run_test tr_mr5raten_4f21d731 verified absence. |
| DONT3 | avoidance | No direct jCodeMunch feature table likely to go stale. | respected | Direct jCodeMunch feature table removed; adv_run_test tr_mr5raten_4f21d731 verified absence. |
| DONT4 | avoidance | No invented competitor claims. | respected | No competitor claims remain in README modified positioning; reviewer READY. |
| DONT5 | avoidance | No unsupported benchmark or adoption-number claims for lgrep. | respected | No unsupported benchmark/adoption-number claims added; reviewer removed unsupported resource numerics; static check passed. |

