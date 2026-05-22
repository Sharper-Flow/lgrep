# Agreement

## Objectives
- Semantic search for `~/dev/advance` and `~/dev/lgrep` is reliable under the local Vision/OpenCode agent workflow.
- Live semantic queries do not synchronously create/replace expensive LanceDB indexes when that can exceed MCP deadlines.
- Agents receive structured lgrep errors before Vision/OpenCode transport deadlines whenever lgrep itself times out.
- Agent guidance recommends a vector-only retry after a hybrid semantic timeout before falling back to text/symbol search.
- Local Vision lgrep configuration supports ADV worktree-heavy usage.

## Acceptance Criteria
1. `lgrep_search_semantic` on a populated cache with >10k chunks does not create/replace FTS/vector indexes on the live query hot path.
2. If hybrid search cannot use ready indexes, lgrep returns useful vector-only results or a structured, actionable error within the configured lgrep timeout.
3. The configured lgrep server timeout is lower than the proxy/tool deadline so agents see lgrep-owned errors instead of `context deadline exceeded` where possible.
4. Local setup docs or checked-in examples include `LGREP_WORKTREE_DEDUP`, explicit warm paths, and timeout guidance for Vision/OpenCode usage.
5. Tool-selection guidance says: after one hybrid semantic timeout/deadline, retry once with `hybrid:false` and a small limit, then fall back to symbol/text/read.
6. Tests cover the hybrid hot-path guard/fallback and timeout guidance.

## Constraints
- Preserve existing semantic and symbol MCP tool names.
- Preserve stdio/local defaults for general users.
- Do not add symlink/env hacks as substitutes for code-level latency control.
- Keep behavior structural: readiness state, config, tests; no prose-only fix.

## Avoidances
- Do not mask all search failures as generic timeouts.
- Do not require users/agents to manually run indexing before first semantic search.
- Do not make every cached repo warm automatically in this local setup.
