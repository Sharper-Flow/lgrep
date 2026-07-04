# Contract Traceability

**Change ID:** updateReadmePositioning2
**Contract Version:** 1
**Rigor:** standard
**Reviewed:** 2026-07-04T02:42:42.129Z

## Contract Items

| ID | Kind | Status | Evidence Policy | Evidence |
| --- | --- | --- | --- | --- |
| SC1 | success_criterion | pass | review | README lines 4, 29-31, 40-61 position lgrep as local-first code intelligence for repos on disk and changing during active agent work; reviewer verdict READY. |
| SC2 | success_criterion | pass | review | README lines 106-113 describe best-fit workflow: ask by meaning, inspect files/outlines, retrieve exact symbol/text. |
| SC3 | success_criterion | pass | review | Direct jCodeMunch comparison section/table removed; final static check tr_mr5raten_4f21d731 found no jCodeMunch/jcodemunch references in README. |
| SC4 | success_criterion | pass | review | README states positive lgrep lane; final static check tr_mr5raten_4f21d731 found no searchcode references. |
| AC1 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 asserted required local-first/evolving-code phrases are present. |
| AC2 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 asserted jCodeMunch/jcodemunch absent; direct comparison removed from README. |
| AC3 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 asserted searchcode absent from README. |
| AC4 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 asserted semantic engine, symbol engine, local LanceDB storage, local JSON index, outlines/workflow phrases present; reviewer confirmed implemented-behavior claims only. |
| AC5 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 asserted forbidden unsupported numeric/adoption/benchmark terms absent; reviewer removed resource numerics. |
| AC6 | acceptance_criterion | pass | test | adv_run_test tr_mr5raten_4f21d731 passed README-focused static check and git diff --check; reviewer verdict READY. |
| C1 | constraint | respected | static_check | README diff is concise and product-facing; reviewer READY with no blockers. |
| C2 | constraint | respected | static_check | README claims limited to implemented behavior verified by adv_run_test tr_mr5raten_4f21d731 and reviewer READY. |
| C3 | constraint | respected | static_check | Only README.md touched in checkpoint commits 035987a and 7b9ad3a; no code files changed. |
| C4 | constraint | respected | static_check | README headline and intro preserve OSS/local-first positioning: Local-first code intelligence, local repos, local storage. |
| C5 | constraint | respected | static_check | Only current repo README.md changed in ADV worktree. |
| DONT1 | avoidance | respected | review | No graph/call hierarchy/impact analysis or remote public repo implementation added; docs-only README change. |
| DONT2 | avoidance | respected | review | No searchcode mention; adv_run_test tr_mr5raten_4f21d731 verified absence. |
| DONT3 | avoidance | respected | review | Direct jCodeMunch feature table removed; adv_run_test tr_mr5raten_4f21d731 verified absence. |
| DONT4 | avoidance | respected | review | No competitor claims remain in README modified positioning; reviewer READY. |
| DONT5 | avoidance | respected | review | No unsupported benchmark/adoption-number claims added; reviewer removed unsupported resource numerics; static check passed. |

## Task References

| Task | Implements | Verifies | Respects | N/A Reason |
| --- | --- | --- | --- | --- |
| tk-afcb776da4f0 | SC1, SC2, SC3, SC4, AC1, AC2, AC3, AC4, AC5 |  | C1, C2, C3, C4, C5, DONT1, DONT2, DONT3, DONT4, DONT5 |  |
| tk-92fc0bbcf001 |  | AC1, AC2, AC3, AC4, AC5, AC6 | DONT1, DONT2, DONT3, DONT4, DONT5 |  |
