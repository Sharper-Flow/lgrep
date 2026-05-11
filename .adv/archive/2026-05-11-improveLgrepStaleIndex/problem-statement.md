# Problem Statement

Agents have been struggling with stale lgrep indexes. Current evidence in this repo shows:

- Semantic watcher can be off (`watching: false`), so edits do not refresh automatically.
- `lgrep_status_semantic(path="")` returns a malformed all-project response: nested project entries omit required `disk_cache` and `error`, causing validation errors and blocking diagnosis.
- `index_symbols_folder(incremental=True)` starts from existing indexed files/symbols but does not remove files absent from current discovery, so deleted-file symbols can remain stale.
- Semantic results include `.adv/changes` and `.adv/archive` proposal/change state, which can surface stale planning text ahead of current implementation.

This makes lgrep less reliable as first-action code intelligence for agents.