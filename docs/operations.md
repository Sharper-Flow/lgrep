# Operating lgrep

Operational runbook for lgrep: response contracts, cache maintenance, git
worktree deduplication, freshness budgets, and shared-daemon tuning. Setup and
the tool/CLI/environment reference live in the [README](../README.md).

## MCP response format

Since `3.0.0`, every lgrep MCP tool returns a structured dict matching a
declared TypedDict in
[`src/lgrep/server/responses.py`](../src/lgrep/server/responses.py). Consume
responses as native dicts — no `json.loads` is needed.

Example — `search_semantic`:

```python
{
    "query": "authentication flow",
    "path": "/path/to/project",
    "engine": "hybrid",
    "total": 1,
    "results": [
        {"file_path": "src/auth.py", "start_line": 42, "end_line": 87,
         "score": 0.91, "match_type": "hybrid",
         "snippet": "def login(username, password):\n    \"\"\"Handle user login.\"\"\"\n    if username..."},
    ],
    "is_stale": False,
    "_meta": {"tool": "search_semantic", "timing_ms": 412.7},
}
```

`total` always equals `len(results)`; it is not the corpus chunk count.
`is_stale` is `True` when the staleness check found drift and scheduled a
background refresh. `_meta` carries the tool name and timing on the
responses whose declared type includes it. `ToolError` and some light
responses — `status_semantic` with no `path`, for example — carry no
`_meta`.

Hits are compact by default: the path, the correct line range, the score, the
match type, and a 3-line snippet (first 3 non-blank chunk-body lines, 120
characters each). Pass `include_content=true` to add the full stored chunk
text as `content` on each hit.

`engine` is `"hybrid"` when `hybrid=true` (the default) or `"vector"` when
`hybrid=false`.

Error responses use the shared `ToolError` shape:

```python
{"error": "VOYAGE_API_KEY not set. Cannot perform semantic search."}
```

Before `3.0.0`, tools returned these objects as `json.dumps(...)` strings. If
you upgrade from `2.x`, remove any `json.loads(response)` wrappers on tool
output. See [Upgrade from 2.x](../CHANGELOG.md#upgrade-from-2x) in the
changelog for the full migration path.

## Argument names and normalization

Each concept carries one declared name in every tool schema:

| Concept | Declared name |
|---|---|
| Search text or symbol name | `query` |
| Local repository root or file | `path` |
| Result cap | `limit` |
| File input/parse budget (unchanged symbol-index files do not count) | `max_files` |

Before validation, the server renames a fixed set of legacy spellings to the
declared name: `max_results`/`maxResults` → `limit`, `symbol`/`symbol_name` →
`query`, `pattern` → `query` on `search_text` only, and
`file_path`/`file`/`folder` → `path`. A synonym applies only when the tool
declares the canonical name and the caller did not send both spellings. Any
other undeclared argument is refused with the tool's valid argument names;
`symbol_id` on `search_references` and `path` on `index_symbols_repo` are
refused with the tool that declares them (`get_symbol` and
`index_symbols_folder`).

## Cache maintenance

Both stores have a dry-run-by-default pruner and a `gc` combiner. Deletion is
irreversible; read the guards before using `--execute`.

### `lgrep prune-orphans` — orphan semantic caches

```bash
lgrep prune-orphans --dry-run
lgrep prune-orphans --execute --cache-dir /path/to/cache
```

- Dry-run by default; `--execute` deletes. `--execute` and `--dry-run` are
  mutually exclusive; passing both exits with an error.
- `--cache-dir` overrides `LGREP_CACHE_DIR` for a single run.
- **Grace window.** Recently modified cache dirs are preserved for 1 hour by
  default so the pruner cannot race a live indexer. Override with
  `LGREP_PRUNE_MIN_AGE_S=<seconds>` (`0` disables grace entirely). The
  `missing_meta` and `project_path_enoent` reasons bypass the grace check
  because they are unambiguous.
- **Guards.** Deletion is refused for any path outside the resolved cache
  directory (path-confinement guard) and for any symlinked cache entry
  (TOCTOU guard) — both show up in `failures[]` rather than as successful
  deletes.
- **Failure handling.** Each orphan is deleted independently. If
  `shutil.rmtree` fails for one entry (a lingering file lock or permission
  issue, for example), the batch continues and the failure is recorded in the
  response under `failures[]` as `{path, error}`; the rest of the reclaim
  still lands. Re-run `lgrep prune-orphans --execute` after addressing the
  error, or inspect with `--dry-run` first to confirm the orphan is still
  present.

### `lgrep prune-symbols` — stale symbol-store indexes

```bash
lgrep prune-symbols --dry-run
lgrep prune-symbols --execute --storage-dir /path/to/storage
```

- Dry-run by default; `--execute` deletes. `--execute` and `--dry-run` are
  mutually exclusive; passing both exits with an error.
- `--storage-dir` overrides `LGREP_SYMBOLS_DIR` for a single run (default:
  `~/.cache/lgrep/symbols/`).
- Deletes stale `index_<hash>.json` files along with their metadata sidecars,
  orphaned sidecars, and stale temp files left by interrupted writes. Lock
  files (`.index_<hash>.lock`) are never removed.
- **Grace window.** Recently modified index files are preserved for 1 hour by
  default (`LGREP_PRUNE_MIN_AGE_S`, `0` disables). Only the
  `unreadable_index_json` reason is grace-eligible; the `repo_path_enoent` and
  `missing_repo_path_field` reasons bypass grace because they are
  unambiguous. Orphan metadata sidecars and stale temp files are also
  grace-eligible.
- **Guards and failure handling** match `prune-orphans`: path-confinement and
  symlink (TOCTOU) refusals and per-entry failures land in `failures[]` while
  the batch continues; re-run after addressing the error.

### Destructive MCP calls need an explicit grant

The MCP tools `prune_orphans`, `prune_symbols`, `invalidate_cache`, and
`invalidate_worktree_cache` coerce `dry_run=True` (or refuse outright) unless
`LGREP_ALLOW_DESTRUCTIVE_MCP` is set in the **server's** environment, and the
refused response carries a `refused_reason` naming the grant. Transport kind
is not consulted: a proxy can front a local stdio pipe with a shared network
port, so `stdio` proves nothing about who is calling. Leave the grant unset on
any shared deployment — including a Vision-proxied server, where the
subprocess transport still reports `stdio` — and run the CLI
(`lgrep prune-orphans --execute` / `lgrep prune-symbols --execute`) so the
operator is explicit. The `invalidate_cache` and `invalidate_worktree_cache`
tools have no CLI equivalent.

MCP pruner calls also skip projects currently loaded in the running server.
`prune_orphans` additionally skips the `symbols/` cache;
`prune_symbols` skips non-local `github:` entries.

### `lgrep gc` — combined garbage collection

```bash
lgrep gc --execute        # or --dry-run (default)
```

Runs three passes over both on-disk stores:

1. `prune_orphans` — deletes whole cache directories whose project root no
   longer exists on disk.
2. `gc_worktree_meta` — removes stale alias entries from `project_meta.json`
   files and deletes the overlay rows of worktrees that no longer exist
   (worktrees deleted without calling `invalidate_worktree_cache`).
3. `prune_symbols` — deletes stale symbol-store index files whose `repo_path`
   is missing, unreadable, or absent from the JSON, plus their sidecars and
   stale temp files.

The `prune_orphans` pass respects the 1-hour grace window
(`LGREP_PRUNE_MIN_AGE_S`), and the `prune_symbols` pass respects the same
window for `unreadable_index_json` only. The `gc_worktree_meta` pass has no
grace window: it removes the alias and overlay rows of every worktree whose
directory is missing when the pass runs. `lgrep gc` runs outside the server,
so no pass skips projects that a running server holds in memory; only the MCP
`prune_orphans` and `prune_symbols` tools apply that skip. `--cache-dir` and
`--symbols-dir` override the store locations for one run. Run
`lgrep gc --execute` periodically (or from a systemd timer).

## Git worktree deduplication

Without dedup, multiple checkouts of one repository accumulate duplicate
semantic indexes — one per worktree path — wasting disk (hundreds of MB per
worktree) and Voyage API tokens.

Enable it by setting `LGREP_WORKTREE_DEDUP` to any non-empty value in the
server environment (an empty value leaves dedup off):

```bash
export LGREP_WORKTREE_DEDUP=1
```

lgrep resolves each project path through `git rev-parse --git-common-dir` and
uses the repository root (parent of `.git`) as the cache key. All worktrees of
one repository share one LanceDB cache directory and one open table, and each
worktree still searches its own files:

- **Base rows** hold the trunk checkout's files.
- **Overlay rows** hold, for each linked worktree, only the files that differ
  from base or that base lacks. A worktree's first index embeds that
  difference and nothing else.
- A worktree search returns its overlay rows plus the base rows of files it
  has not changed, in one prefiltered query. Base files the worktree changed
  or deleted are hidden. Trunk search returns base rows only.
- When base rows change (after a trunk pull, for example), each worktree
  compares every file with base again on its next search or index pass.
- Stale-file cleanup runs for every checkout: deleting a file removes its
  base rows on trunk and hides them in a worktree.
- `project_meta.json` lists every worktree that uses the cache in
  `alias_paths`.

Caches created before overlay rows existed gain a `checkout` column when
opened; existing rows become base rows with no re-embedding.

**Concurrency:** cross-process alias updates to `project_meta.json` are
guarded by a POSIX advisory lock (`fcntl.flock`) on a dedicated `.meta.lock`
file, so simultaneous writes from multiple lgrep instances do not lose alias
entries.

**Tearing down a worktree:** call the `invalidate_worktree_cache` MCP tool
when archiving a worktree to remove its alias and overlay rows from the
shared cache:

```text
invalidate_worktree_cache(paths: ["/path/to/worktree"])
```

## Freshness and index budgets

- `search_semantic` runs an auto-staleness check before every search. When
  file mtimes have moved past the index timestamp and content hashes have
  drifted, the search serves the current (possibly slightly stale) index
  immediately and schedules a background single-flight refresh — it never
  blocks on a full re-embed. The response sets `is_stale: true` when the
  check found drift. The search does not wait for the refresh, so a later
  search still returns stale results while the refresh runs, or if it fails.
  Results become fresh once a refresh completes, with no operator
  configuration.
- `watch_start_semantic` (`LGREP_AUTO_WATCH`) is an incremental-freshness
  **optimization** (per-file background re-index on edit), not a correctness
  dependency. `index_semantic` is only needed for first-time setup or to
  force a guaranteed-fresh refresh before a specific query.
- `LGREP_STALENESS_DEADLINE_S` (default `4.0`) bounds the whole staleness
  check. `LGREP_ENSURE_BUDGET_S` (default `8.0`, keep below
  `LGREP_TOOL_TIMEOUT_S`) bounds the time a worktree search may spend making
  its trunk's base index current before answering; `0` always defers to the
  background. `LGREP_INDEX_MAX_WALL_S` (default `60.0`) bounds each indexing
  window. `LGREP_AUTO_REFRESH=0` opts out of the symbol-index refresh gate;
  every other value keeps it on.
- **`Repository not indexed` from symbol tools:** local git checkouts build
  their own index on the first symbol query (`search_symbols`, `get_symbol`,
  `get_symbols`, `search_references`). Seeding is root-to-root: when the
  queried path is a checkout root and a linked worktree of the same
  repository already has an index, the new index is seeded from it and an
  incremental refresh re-parses only files whose content hash differs, so the
  answer always comes from the queried checkout's own files. Queries against a
  subdirectory of a checkout still report `Repository not indexed` — index
  `index_symbols_folder(path=...)` explicitly in that case. Indexes for
  checkouts that no longer exist on disk are deleted when a new index is
  created. Run `index_symbols_folder(path=...)` explicitly for non-git
  folders, to raise `max_files`, or to force a rebuild.
- Symbol indexing limits changed/new file parses per call with `max_files`.
  Unchanged files are checked but do not consume that parse budget, so a
  refresh can reach edited files after an unchanged prefix. Source retrieval
  validates each stored symbol's name, kind, parent, and complete byte range
  against one parsed file snapshot. A mismatched range returns an error
  instead of another symbol's body; re-index before retrying. Batch retrieval
  shares one snapshot per file and reports mismatches per symbol.
- Symbol search holds its checked index for that query rather than reloading
  it after each step. An unchanged search loads the body once; a refresh loads
  again under the writer lock and returns its resulting snapshot directly.
  This also applies to indexes too large for the global retention budget.
  An unchanged-content refresh advances only the index file's check timestamp,
  without rewriting symbols or occurrences. It stamps two seconds before walk
  start. This covers coarse kernel timestamp lag or whole-second quantization,
  not FAT's two-second filesystem granularity combined with clock lag. lgrep
  makes no FAT timing guarantee. Recent files may cause another no-op refresh
  within that margin. A concurrently removed index does not prevent serving
  the already-loaded snapshot. New worktree seeds still publish their own
  checkout identity. Refreshes that publish a changed body retain their
  publication-time stamp.
- MCP symbol searches share one supervised index check/refresh per repository.
  It uses a separate `symbol` lane with two workers, independent of query and
  semantic-build workers, and appears as a `symbol_index` job in
  `lgrep_diagnostics`. A query waits at most five seconds, or half
  `LGREP_TOOL_TIMEOUT_S` if smaller, for that snapshot. If the check, queued
  job, bootstrap, or refresh takes longer, the query returns
  `code="indexing_in_progress"`, `retryable=true`, and no symbol results.
  The job continues; another query joins it rather than starting another
  writer. Query cancellation does not cancel that shared job. Completed
  snapshots are not retained as another cache. A full symbol lane can also
  produce this response for an unchanged repository, without taking workers
  from text/semantic queries or semantic builds. Python/CLI symbol
  searches still acquire their snapshot synchronously. No automatic retry is
  performed; check diagnostics and query after the job finishes.

## Explicit symbol-index jobs

`index_symbols_folder` and `index_symbols_repo` acknowledge background jobs with
`job_id`, `state`, `duplicate`, and `_meta`, not final indexing counters. Read
`get_symbol_index_job(job_id)` for `queued`, `running`, `completed`, `failed`, or
`cancelled` (shutdown only). Completed status contains `result` with final counters,
target metadata, and truncation; failed status contains a bounded `error`.

Only a recorded `stdio` startup transport admits these three tools. HTTP has no
process-lifetime job context; HTTP, missing context, and unknown transport return
`symbol_index_jobs_unavailable`, retryable=false, without a job ID or work. This
does not change HTTP lifespan behavior or the query-triggered symbol refresh lane.

Each daemon admits one nonterminal explicit job on a dedicated one-worker lane,
independent of query, symbol-refresh, and shared semantic-build workers. An identical
nonterminal submission returns its ID with `duplicate=true`. A different request
returns `symbol_index_busy`, retryable=true, and queues no additional job. Local
writers still share the per-repository storage lock; query-triggered refreshes
have no new busy policy. Disconnecting the submitting client does not stop a job.

`max_files` is the file cap (changed/new parses for incremental folder jobs).
`LGREP_EXPLICIT_INDEX_BUDGET_S` separately defaults to 150 seconds, independent of
`LGREP_TOOL_TIMEOUT_S`. It is read once per supervisor. Unset or empty selects 150
silently. Non-numeric, zero, negative, NaN, or infinite values select 150 and warn
once per supervisor without logging the supplied value. Overrides must be positive
and finite. One admission-time deadline covers resolution, lock wait, load,
discovery, fetch, parse, and publication; no stage receives a fresh budget.

Deadline stops publish a partial result with `truncation_reason="deadline"` and
`walk_truncated=true` (folder) or `truncated=true` (remote). A file-cap stop reports
`"max_files"`; an unstopped run reports null. Folder runs preserve out-of-window
entries under the existing incremental deletion rules. Remote partial publications
still replace the older remote baseline; they do not preserve a last-good index.
A truncated completion is not full repository coverage. For incremental local
work, resubmit to advance another window and inspect status again.

Stopping is cooperative: lock waits, one file, blocking I/O, and an already-started
save can overrun the budget. Shutdown closes admission and signals the stop event.
A queued cancelled job never enters its helper. Running jobs retain ownership
until their worker stops. A stop observed before publication starts no save; an
already-started save finishes or fails normally. There is no preemption, rollback,
public cancellation tool, or hard shutdown-duration guarantee.

Status retains the latest 100 terminal explicit jobs independently of query-job
diagnostics. Later explicit completions evict old records. Random per-supervisor
ID generations prevent an old ID from naming a new job. Unknown, evicted,
wrong-kind, and earlier-daemon IDs return `unknown_symbol_index_job`,
retryable=false, and create no work. Jobs and history do not survive restart;
an unknown ID does not prove the old job completed. Inspect the index and resubmit
as needed. Credentials and private request fingerprints are never status fields.

## Shared-daemon tuning (Vision / OpenCode)

For agent-heavy local setups that route lgrep through a Vision-managed MCP
server, prefer explicit warm paths over warming every cached repository:

```yaml
lgrep:
  port: 6278
  command: /home/you/.local/bin/lgrep
  env:
    VOYAGE_API_KEY: "${VOYAGE_API_KEY}"
    LGREP_WORKTREE_DEDUP: "1"
    LGREP_WARM_PATHS: "/home/you/dev/primary:/home/you/dev/tooling"
    LGREP_AUTO_WARM_DISK: "false"
    LGREP_TOOL_TIMEOUT_S: "8"
    LGREP_WORKER_MAX_THREADS: "4"
```

- `LGREP_WORKTREE_DEDUP=1` avoids duplicate semantic caches for git worktrees.
- `LGREP_WARM_PATHS` should name only repos agents actively search.
- `LGREP_AUTO_WARM_DISK=false` prevents surprise startup work from old cache
  entries.
- Set `LGREP_TOOL_TIMEOUT_S` below the MCP proxy/client timeout so callers get
  a structured lgrep error before a transport deadline.
- Keep `LGREP_WORKER_MAX_THREADS` small for shared daemons so concurrent
  agents cannot create unbounded blocking work.
- Leave `LGREP_BUILD_MAX_THREADS` at `1` on shared daemons: builds are
  background work and a single lane keeps them behind queries.
- Use `lgrep_diagnostics` when investigating high CPU/thread count. It reports
  PID, uptime, loaded projects, worker limit, active jobs, recent
  abandoned/finished jobs, and full local project paths without exposing API
  keys or environment values.
- The server retains at most 20 canonical semantic-cache owners, including
  assemblies and stores that are still retiring. Worktree views share an owner.
  A new owner replaces the least recently used idle owner without restarting
  the server. Complete calls and physical workers, including workers whose
  callers timed out, keep their owners active. Eviction preserves disk data;
  the next query reopens an unchanged index without indexing or embedding.
- Eviction stops an idle owner's watchers and cancels pending watcher events.
  Reopening does not restart those watchers. Use `watch_start_semantic` if
  incremental file watching is still wanted. Search staleness checks remain
  available independently of watching.
- If every owner slot is active or retiring, admission returns
  `code: "project_capacity_busy"` and `retryable: true`. Retry after active work
  finishes; a restart is not the normal remedy. A watcher stop failure keeps
  its slot reserved and logs `owner_store_retirement_failed`.
  That owner remains unavailable until the server shuts down. Inspect the
  logged failure before considering a restart.
- `owner_stores` in diagnostics reports `cap`, `open`, `assembling`, `retiring`,
  `busy`, `idle`, and cumulative `evictions`. Retiring owners are not counted
  as open. `busy + idle = open`; `open + assembling + retiring` never exceeds
  `cap`. These counts do not include symbol-cache entries or count each
  checkout alias separately.
- A connection or table-open failure returns an error without deleting the
  last-good cache. An error alone does not authorize destructive recovery.
  Existing model/schema migration checks still apply when a table opens.
- Its `symbol_cache` snapshot reports process-global `entries`, `tracked_bytes`,
  and `limit_bytes` without reading index files. The 64 MiB limit counts retained
  serialized index bytes, not parsed heap or RSS. In-flight callers can keep
  evicted objects alive. Indexes larger than the limit remain readable from disk
  but bypass retention, so repeated queries on those indexes parse them again.
- `status_semantic(path="")` is intentionally cheap and memory-only. Pass a
  specific `path` when you need deep file/chunk counts.
- Destructive cache cleanup over MCP requires the explicit server-side
  `LGREP_ALLOW_DESTRUCTIVE_MCP` grant; without it the MCP tools return a
  preview/refusal. Run `lgrep prune-orphans --execute` (or
  `lgrep prune-symbols --execute` for symbol indexes) from a local shell when
  an operator intentionally wants deletion.

**Agent fallback rule:** if a default hybrid `search_semantic` call times out
or hits a deadline, retry once with `hybrid:false` and a small limit such as
`limit=5`, then fall back to `search_symbols`, `search_text`, or direct file
reads.

## Notes on selected environment variables

- `LGREP_LOG_FILE` — opt-in rotating JSON log file (10 MiB, 3 backups) in
  addition to the always-on stderr sink. One writer is enforced by an
  exclusive `flock` on a `<file>.lock` sidecar held for process life; a
  second writer warns on stderr and keeps stderr-only logging. stdout is
  never a log sink (the stdio MCP channel owns it).
- `LGREP_GITHUB_TOKEN` — used by `index_symbols_repo` when no token is passed
  to the call. Lifts remote indexing from the anonymous 60/hour rate limit
  (shared across all sessions) to the authenticated 5000/hour limit.
  `GITHUB_TOKEN` is used as a fallback; an explicit `github_token` argument
  wins over both.
- `LGREP_ALLOW_DESTRUCTIVE_MCP` — accepted values are `true`, `1`, and `yes`
  (case-insensitive). See the grant rules under [Cache maintenance](#cache-maintenance).
- `LGREP_WORKER_MAX_THREADS` / `LGREP_BUILD_MAX_THREADS` — unset or
  non-numeric values fall back to the defaults (`4` and `1`); numeric values
  are clamped to at least `1`.
