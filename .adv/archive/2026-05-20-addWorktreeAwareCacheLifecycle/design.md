# Design: Worktree-Aware Cache Lifecycle

## Architecture

### 1. Canonical Cache Key Resolution

**Module:** `storage/_chunk_store.py`

New function `canonical_repo_key(project_path: Path) -> Path`:

```python
def canonical_repo_key(project_path: Path) -> Path:
    """Resolve the canonical cache key for a project path.

    When LGREP_WORKTREE_DEDUP is enabled and the path is inside a git worktree,
    returns the git common-dir parent (i.e., the repo root). Falls back to
    Path.resolve() when not under git or when the flag is off.

    Uses --path-format=absolute to guarantee absolute output (Git >= 2.30).
    """
    resolved = project_path.resolve()

    if not os.environ.get("LGREP_WORKTREE_DEDUP"):
        return resolved

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(resolved),
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            common_dir = Path(result.stdout.strip())
            # common_dir is typically /path/to/repo/.git
            # The repo root is its parent
            if common_dir.name == ".git":
                return common_dir.parent
            # Linked worktrees may return paths like
            # /path/main/.git/worktrees/name — walk up to the .git level
            for parent in common_dir.parents:
                if parent.name == ".git":
                    return parent.parent
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return resolved
```

**Integration into `get_project_db_path`:**

```python
def get_project_db_path(project_path: str | Path) -> Path:
    key = canonical_repo_key(Path(project_path))
    path_hash = hashlib.sha256(str(key).encode()).hexdigest()[:12]
    cache_dir = Path(os.environ.get("LGREP_CACHE_DIR", DEFAULT_CACHE_DIR))
    return cache_dir / path_hash
```

**Performance:** `git rev-parse` is sub-millisecond (reads `.git` file only). Forked once per project init, memoized on `ProjectState.canonical_key`. Amortized cost: zero.

**Edge cases:**
- Bare repos: `--git-common-dir` returns the repo dir itself. `common_dir.parent` would be wrong, but ADV never indexes bare repos. Fallback to `Path.resolve()` handles this naturally (bare repo has no `.git` dir name).
- Submodules: Returns submodule's `.git/modules/` path. Different worktrees of the same parent repo have different `.git/modules/` paths, so submodule dedup doesn't work. Acceptable — submodules are rare in ADV workflows and get their own small caches.
- Non-git paths: `git rev-parse` fails → falls back to `Path.resolve()`. Zero behavior change.

### 2. Stale-File Deletion Guard (MANDATORY FIX)

**Module:** `indexing.py`

**Problem (validator CONFLICT):** `index_all()` lines 78-87 compute `stale_files = indexed_files - current_rel_paths` and delete chunks for files absent from the current worktree. With shared LanceDB, worktree B on a different branch would delete worktree A's file chunks — corrupting search results.

**Fix:** When `LGREP_WORKTREE_DEDUP` is enabled, skip the stale-file deletion pass entirely:

```python
# In Indexer.__init__:
self._dedup_enabled = bool(os.environ.get("LGREP_WORKTREE_DEDUP"))

# In index_all():
# Remove stale chunks for files that no longer exist on disk
# SKIP when worktree dedup is enabled — shared LanceDB means files
# absent from THIS worktree may be present in another worktree's checkout.
if not self._dedup_enabled:
    try:
        indexed_files = self.storage.get_indexed_files()
        current_rel_paths = {str(Path(f).relative_to(self.project_path)) for f in all_files}
        stale_files = indexed_files - current_rel_paths
        for stale_path in stale_files:
            self.storage.delete_by_file(stale_path)
            log.info("stale_file_removed", file=stale_path)
    except Exception as e:
        log.warning("stale_cleanup_failed", error=str(e))
```

**Tradeoff:** With dedup enabled, deleted-file chunks remain in the shared cache until a full rebuild. This is acceptable because:
1. Stale chunks don't corrupt search results (they return extra results, not wrong results)
2. The disk cost is bounded (deleted files are small relative to total index)
3. A `lgrep gc --rebuild` can force a clean rebuild when needed

### 3. Project Metadata for Worktree Aliasing

**Module:** `storage/_chunk_store.py`

`write_project_meta` gains optional `alias_paths` field:

```json
{
    "project_path": "/home/jon/dev/lgrep",
    "alias_paths": [
        "/home/jon/.local/share/opencode/worktree/.../change/fix-auth"
    ],
    "updated_at": 1716172800.0
}
```

**Concurrent-write note:** The `alias_paths` update is a read-modify-write cycle. Within a single lgrep MCP process, the `asyncio.Lock` in `_ensure_project_initialized` serializes all inits, so no race. Across separate lgrep processes (two OpenCode sessions), a lost alias is possible but self-healing (next init re-adds it) and non-catastrophic (only affects discovery, not search correctness). Documented in code comment.

### 4. Worktree Cache Invalidation MCP Tool

**Module:** `server/tools_maintenance.py`

New async tool `invalidate_worktree_cache`:

```python
@mcp.tool(
    description="Invalidate worktree-specific cache entries. Removes the "
    "worktree alias from project_meta.json and unloads from server memory. "
    "Does NOT delete the canonical LanceDB cache.",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@time_tool
async def invalidate_worktree_cache(
    paths: Annotated[list[str], Field(description="Worktree paths to invalidate")],
    ctx: Context | None = None,
) -> WorktreeInvalidationResult:
```

**Security guards (same pattern as prune_orphans):**
- Resolve each path, compute cache dir via `get_project_db_path`
- Path confinement: resolved cache dir must be under `LGREP_CACHE_DIR`
- Refuse symlinks (TOCTOU guard)

**Behavior per path:**
1. Compute `get_project_db_path(path)` → find cache dir
2. Read `project_meta.json` → remove path from `alias_paths`
3. If `alias_paths` empty AND canonical `project_path` doesn't exist on disk → delete entire cache dir
4. If canonical exists → update meta only, keep cache
5. Remove path from `LgrepContext.projects` if loaded

**Response type** (`server/responses.py`):
```python
class WorktreeInvalidationEntry(TypedDict):
    path: str
    cache_dir: str
    alias_removed: bool
    cache_deleted: bool
    bytes_reclaimed: int
    error: str | None

class WorktreeInvalidationResult(TypedDict):
    paths_cleaned: int
    bytes_reclaimed: int
    entries: list[WorktreeInvalidationEntry]
    _meta: _Meta
```

### 5. Background Orphan Sweep on Server Start

**Module:** `server/lifecycle.py`

```python
async def _schedule_startup_sweep(ctx: LgrepContext) -> None:
    """One-shot orphan sweep after warmup delay."""
    await asyncio.sleep(300)  # 5-minute warmup
    log.info("startup_orphan_sweep_begin")
    active_set = list(ctx.projects.keys())
    report = await asyncio.to_thread(
        _prune_orphans, dry_run=False, active_set=active_set
    )
    log.info(
        "startup_orphan_sweep_done",
        deleted=report["deleted_dirs"],
        reclaimed_bytes=report["reclaimed_bytes"],
    )
```

Scheduled in `app_lifespan` as fire-and-forget:
```python
async def app_lifespan(server: FastMCP) -> AsyncIterator[LgrepContext]:
    ctx = await _startup(server)
    await _warm_projects(ctx)
    sweep_task = asyncio.create_task(_schedule_startup_sweep(ctx))
    try:
        yield ctx
    finally:
        sweep_task.cancel()
        await _shutdown(ctx)
```

**Timing:** 5-minute delay + 1-hour grace window = no race with slow indexing. One-shot, not recurring.

### 6. `lgrep gc` CLI Subcommand

**Module:** `cli.py`

New dispatch: `if args and args[0] == "gc": return _cmd_gc(args[1:])`

`_cmd_gc` wraps:
1. `prune_orphans(dry_run=...)` — existing orphan cleanup
2. `gc_worktree_meta()` — new helper that scans all `project_meta.json` files and removes alias entries whose paths no longer exist on disk

Flag pattern matches `_cmd_prune_orphans`: `--execute`, `--dry-run`, `--cache-dir`.

## LBP Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Cache key function | Hash of git common-dir parent | Structural (P33): repo identity, not filesystem materialization |
| git flag | `--path-format=absolute --git-common-dir` | Eliminates relative/absolute ambiguity (Git ≥ 2.30) |
| Stale-file handling with dedup | Skip deletion pass entirely | Prevents cross-worktree chunk corruption; stale chunks are benign |
| Opt-in mechanism | Env var only (`LGREP_WORKTREE_DEDUP`) | Consistent with lgrep's 8+ existing env vars |
| Fork cost | Once per project init, cached on `ProjectState` | Sub-ms git rev-parse, amortized over session |
| Meta alias storage | Array in `project_meta.json` | Reuses atomic write; concurrent race is low-impact and self-healing |
| In-memory dedup | Deferred to follow-up | Disk dedup reclaims dominant cost; ~5-25MB Python overhead per extra ProjectState is negligible |
| Background sweep timing | 5-min delay, one-shot, fire-and-forget | Rare event; grace window protects active indexers |
| Invalidations tool accepts list | Batch path support | Matches ADV's N-worktree-at-once pattern |

## Implementation Strategy (Ordered)

1. **`canonical_repo_key()` + `get_project_db_path` refactor** — core change
2. **Stale-file deletion guard** — mandatory correctness fix
3. **`write_project_meta` alias support** — multi-path tracking
4. **`invalidate_worktree_cache` MCP tool** — ADV integration
5. **Background sweep** — lifecycle.py
6. **`lgrep gc` CLI** — operator convenience
7. **Tests** — unit + integration per layer
8. **Docs** — README + ADV integration note

## Validator Assessment Summary

| Question | Verdict | Resolution |
|---|---|---|
| `git rev-parse --git-common-dir` correctness | VALIDATED | Use `--path-format=absolute` (adopted) |
| Fork-once-memoize strategy | VALIDATED | No changes needed |
| `alias_paths` concurrent writes | CAUTION | Document race; self-healing; low impact |
| Deferred in-memory dedup + stale-file deletion | CONFLICT | **Mandatory fix adopted**: skip stale-file deletion when dedup enabled |
| 5-minute sweep delay | VALIDATED | No changes needed |
| LanceDB multiple connections | CAUTION | Single-process server + asyncio.Lock sufficient for v1 |

## Error Handling

| Scenario | Behavior |
|---|---|
| `git rev-parse` fails (not a git repo) | Silent fallback to `Path.resolve()` |
| `git rev-parse` times out | 2s timeout → fallback |
| `project_meta.json` corrupted during alias update | Re-read and retry once; log warning |
| Cache dir missing during invalidation | Return `alias_removed: False`, no error |
| Path outside cache root during invalidation | Refuse, return error entry |
| Race between sweep and active indexer | Grace window (1h) protects |
| Concurrent alias writes (multi-process) | Last-writer-wins; self-healing on next init; documented |
