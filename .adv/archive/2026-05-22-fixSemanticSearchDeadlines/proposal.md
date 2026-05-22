# Fix semantic search deadlines

## Summary
Make lgrep semantic search reliable for agent use in the local Vision/OpenCode setup by removing expensive hybrid-index creation from the live query path, tuning server configuration for the repos agents actually use, and documenting/encoding timeout fallback behavior.

## Problem
Agents in `~/dev/advance` can hit `retry_exhausted: context deadline exceeded` when `lgrep_search_semantic` performs cold hybrid search work. The service is available and indexed, but hybrid search may create LanceDB FTS/vector indexes during the user query, exceeding the MCP proxy deadline before lgrep can return a structured response.

## Scope
- Guard LanceDB hybrid index creation so search remains latency-bounded.
- Add deterministic fallback behavior for missing/unready hybrid indexes.
- Tune local Vision lgrep configuration for ADV/lgrep usage.
- Update lgrep agent/tool guidance to prefer vector-only retry after semantic timeout.
- Add tests covering cold-cache/search latency and fallback behavior.

## Success Criteria
- Hybrid semantic search no longer creates/replaces LanceDB indexes synchronously on the live query path.
- Timeout behavior returns structured lgrep-owned errors before Vision/OpenCode proxy deadlines where possible.
- Agents have documented fallback: retry semantic once with `hybrid:false`, then use symbol/text/read.
- Local Vision lgrep config warms only the repos we actively use and enables worktree dedup.
- Tests and manual probes verify the behavior on `~/dev/advance`.

## Out of Scope
- Replacing Voyage embeddings.
- Replacing LanceDB.
- Broad ranking-quality redesign beyond timeout/fallback correctness.
