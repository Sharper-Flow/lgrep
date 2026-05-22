# Design

## Direction
Prioritize structural latency control over instruction-only changes.

## Confirmed Evidence
- Local Vision config tuning was applied first and verified: lgrep now warms only `/home/jon/dev/advance`, `/home/jon/dev/lgrep`, and `/home/jon/.local/share/Advance`.
- `lgrep_search_semantic(hybrid:true)` now returns a structured lgrep timeout after `LGREP_TOOL_TIMEOUT_S=8` instead of a Vision `context deadline exceeded`.
- `lgrep_search_semantic(hybrid:false)` returns vector results for `/home/jon/dev/advance`.
- Validator confirmed the code creates/replaces LanceDB FTS/vector indexes on the live hybrid search path.

## Implementation Strategy
1. Preserve local Vision lgrep config tuning.
2. Add index-readiness probing in `ChunkStore` using LanceDB index metadata (`list_indices()` where available) so warm-path loads can detect existing FTS/vector indexes.
3. Move expensive index creation out of `ChunkStore.search_hybrid`.
4. Add a safe search fallback: if hybrid prerequisites are not ready, return vector-only results rather than blocking on index creation.
5. Update lgrep instructions/skill docs so agents retry vector-only once after hybrid semantic timeout/deadline.
6. Add tests around hot-path guard/fallback, index probing, and guidance.

## Design Validator Result
Verdict: PASS WITH CONCERNS.

Required refinements incorporated:
- Probe existing indexes on `ChunkStore` init/warm load.
- Define `index_all`/warmup as index creation locations.
- Treat agent retry guidance as defense-in-depth; server-side fallback is primary.

## Verification
- Unit tests for storage/hybrid index behavior.
- Tool-routing docs tests for fallback instruction.
- Manual MCP probes against `/home/jon/dev/advance` and `/home/jon/dev/lgrep`.
