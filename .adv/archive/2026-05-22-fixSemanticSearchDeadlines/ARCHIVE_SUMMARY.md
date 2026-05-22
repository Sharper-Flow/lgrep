# Archive: Fix semantic search deadlines

**Change ID:** fixSemanticSearchDeadlines
**Archived:** 2026-05-22T05:09:02.840Z
**Created:** 2026-05-22T04:46:03.377Z

## Tasks Completed

- ✅ Add regression tests for semantic deadline behavior
  > Added storage test asserting live hybrid search does not call `create_fts_index`/`create_index` and falls back to vector results. Added tool-routing test asserting packaged instructions mention timeout/deadline retry with `hybrid:false`. RED run failed as expected before implementation.
- ✅ Refactor hybrid index readiness and fallback
  > Added best-effort LanceDB index readiness probing on table open. Added `prepare_hybrid_indexes()` for explicit indexing paths and invoked it after `Indexer.index_all()`. Removed `replace=True` hot-path index creation from `search_hybrid`; when FTS is not ready, search degrades to `search_vector()` with log context. Updated storage tests to reflect explicit preparation.
- ✅ Update setup and agent guidance for Vision/OpenCode lgrep usage
  > Documented Vision/OpenCode tuning in README and skill docs: worktree dedup, explicit warm paths, no auto-warm-all-disk, and lgrep timeout below proxy deadline. Updated always-on instruction and skill usage to retry once with `hybrid:false` after hybrid semantic timeout/deadline, then fall back to symbols/text/read.
- ✅ Verify semantic search behavior end-to-end
  > Ran focused and full test suites, lint, and a worktree lgrep CLI probe against `/home/jon/dev/advance`. No additional code changes were needed for verification.

## Specs Modified

