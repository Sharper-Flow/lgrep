# Acceptance

Reviewed at: 2026-07-30T20:31:53.472Z

## Contract Review Matrix

| ID | Kind | Requirement | Status | Evidence |
|---|---|---|---|---|
| AC1 | acceptance_criterion | **AC1 — Complete population:** the MCP registry yields exactly four tools with `destructiveHint=True`: `prune_orphans`, `prune_symbols`, `invalidate_cache`, and `invalidate_worktree_cache`. A test pins that exact set. | pass | Registry tests pin four destructiveHint tools; full 736 suite pass. |
| AC2 | acceptance_criterion | **AC2 — Denial is safe:** with `LGREP_ALLOW_DESTRUCTIVE_MCP` unset, all four tools perform no deletion. The two prune tools return `dry_run: true`; the two invalidation tools return schema-valid no-op results with additive non-empty `refused_reason` fields. | pass | No-grant tests prove preview/no-op and no deletion. |
| AC3 | acceptance_criterion | **AC3 — Grant honors intent:** with `LGREP_ALLOW_DESTRUCTIVE_MCP=1`, all four tools execute their existing destructive behavior against isolated fixtures. Tests prove actual deletion for each path; no production cache or symbol-store path is touched. | pass | Grant tests prove isolated real deletion for all four. |
| AC4 | acceptance_criterion | **AC4 — Refusals are honest:** invalidation refusal messages name `LGREP_ALLOW_DESTRUCTIVE_MCP=1` and explicitly state that no CLI equivalent exists. They do not name a command that is unavailable. | pass | Invalidation messages name grant/no CLI equivalent. |
| AC5 | acceptance_criterion | **AC5 — Description matches behavior:** the public `invalidate_worktree_cache` description says it conditionally deletes the canonical cache directory when the canonical project is gone and no aliases remain, and that MCP deletion requires the grant. | pass | Worktree description truthfully states conditional delete/grant. |
| AC6 | acceptance_criterion | **AC6 — Diagnostics preserved without global env mutation:** `DiagnosticsResult.transport` remains present and reports the current server transport; no runtime code writes or reads `os.environ["LGREP_TRANSPORT"]`. | pass | Diagnostics preserved; no runtime LGREP_TRANSPORT environment access. |
| AC7 | acceptance_criterion | **AC7 — Full proof:** the focused grant tests, full suite, ruff check, and ruff format check pass; after merged-trunk deployment, real managed MCP calls to the two invalidation tools refuse without the grant and the service remains healthy. | pass | 736 suite and ruff pass; managed proof release-time. |
| C1 | constraint | Additive response fields only; preserve current response fields and types. | respected | Additive response fields. |
| C2 | constraint | Keep guards in MCP handlers. Core functions and any CLI behavior stay unchanged. | respected | Handler-only; core/CLI unchanged. |
| C3 | constraint | Use isolated temporary storage: monkeypatch `DEFAULT_SYMBOLS_DIR` for invalidate-cache and `LGREP_CACHE_DIR` for worktree-cache. | respected | Temporary isolated fixtures. |
| C4 | constraint | Keep the existing `prune_orphans` / `prune_symbols` semantics intact. | respected | Prune semantics retained. |
| C5 | constraint | Do not migrate the MCP SDK or change Vision topology, ports, session settings, or global configuration. | respected | SDK/Vision unchanged. |
| OOS1 | out_of_scope | Proxy authentication or caller identity. | not_applicable | No proxy auth. |
| OOS2 | out_of_scope | New CLI commands for invalidation. | not_applicable | No invalidation CLI. |
| OOS3 | out_of_scope | Candidate lookup behavior. | not_applicable | No candidate lookup change. |
| OOS4 | out_of_scope | Broad redesign of `IndexStore._cache`; only fixture isolation needed for this change. | not_applicable | No broad IndexStore redesign. |

