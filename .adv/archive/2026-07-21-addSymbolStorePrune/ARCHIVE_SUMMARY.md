# Archive: Add symbol store prune

**Change ID:** addSymbolStorePrune
**Archived:** 2026-07-21T04:23:21.296Z
**Created:** 2026-07-21T02:36:36.204Z

## Tasks Completed

- ✅ Implement `src/lgrep/tools/prune_symbols.py` — core stale-index detection and prune logic.
  > Task checkpoint completed
- ✅ Register `prune_symbols` MCP tool in `server/tools_maintenance.py` + add `PruneSymbolsResult` TypedDict in `server/responses.py`.
  > Task checkpoint completed
- ✅ Add CLI subcommand `lgrep prune-symbols` and extend `lgrep gc` to invoke the new sweep.
  > Task checkpoint completed
- ✅ Write 6 spec requirements for new capability `lgrepSymbolStoreLifecycle` via `adv_delta_add` calls.
  > Task checkpoint completed
- ✅ Update README + CLI help text to document new `lgrep prune-symbols` subcommand and extended `lgrep gc` behavior.
  > Task checkpoint completed

## Specs Modified

- **lgrep-symbol-store-lifecycle**: 6 delta(s)
