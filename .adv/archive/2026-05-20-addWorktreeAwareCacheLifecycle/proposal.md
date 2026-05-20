## Cross-Project Origin

This change was created as a follow-up from **toolbox**.

| Field | Value |
|-------|-------|
| Source project | toolbox |
| Source path | `/home/jon/toolbox` |

> **Note:** The originating project should be consulted for context on why this change is needed.


## Summary

lgrep keys its semantic cache strictly by resolved absolute project path. This collapses badly under the ADV worktree workflow, where a single git repository is materialized at N concurrent paths (`{repo}` trunk plus per-change worktrees under `~/.local/share/opencode/worktree/{project-id}/change/{branch}`). The result is N duplicate embedding indexes of the same codebase, N× Voyage API token cost on first warmup per worktree, multi-GB RAM bloat when sessions load several worktree indexes simultaneously, and silent on-disk cache leaks after `/adv-archive` deletes the worktree.

This change introduces worktree-aware caching: lgrep recognizes when two project paths share a git common-dir, optionally collapses their semantic indexes onto a canonical key, and exposes a structured archive-hook surface so ADV can invalidate ephemeral worktree caches at change completion. Existing orphan-pruning (already shipped in v2.x) is extended with automatic background sweeps so cache leaks no longer accumulate even when archive hooks are skipped.

## Evidence

Observed in a single OpenCode session at 2026-05-20 01:30 EDT on `anomalyco/opencode`-derived multi-session workflow:

- 8 cache directories under `~/.cache/lgrep/`, totaling 1.6 GB
- 4 of those caches indexed the SAME git common-dir (`/home/jon/dev/repos/advance/.git`) at 4 different worktree paths: trunk + 3 ADV change worktrees. Combined: ~500 MB redundant embeddings (143 + 157 + 101 + 99 MB)
- 3 caches pointed to deleted paths (1 deleted worktree + 1 moved repo path + 1 deleted `/tmp` checkout) — 1.2 GB pure orphans
- Live `lgrep` worker RSS: 2.18 GB, 94 threads, VmSize 8.6 GB — driven by 6 in-memory project indexes for what is structurally 3 logical repos
- No automatic cache GC anywhere. `prune_orphans` exists as a tool but requires manual CLI invocation (`lgrep prune-orphans --execute`) or MCP call

The growth pattern scales linearly with the number of active ADV changes per repo. Projects with healthy ADV throughput (5–10 in-flight changes) will pay 5–10× the embedding cost they should and accumulate orphans on every archive.

## Problem statement

### Cache identity ignores git worktree topology

`get_project_db_path()` in `storage/_chunk_store.py` hashes `Path(project_path).resolve()` as the sole cache key. There is no consideration of `git rev-parse --git-common-dir` or any other repo-identity signal. Two worktrees that share the same `.git` common-dir produce different cache hashes and therefore different LanceDB tables, even though their content is 99% identical at any given moment.

### No archive integration for ephemeral worktrees

ADV's `/adv-archive` Phase 9 deletes the worktree on disk but has no knowledge of lgrep's cache layout. lgrep has no MCP surface that ADV could call to clean up ("here is a worktree path you should invalidate"). The result is silent disk leak on every successful change archive.

### Manual-only orphan pruning

`lgrep.tools.prune_orphans` is correctly implemented (TOCTOU guards, grace window, dry-run default, path-confinement) but it ships as a manual operator tool. There is no scheduled sweep, no startup sweep, no archive trigger, and the MCP variant defensively coerces `dry_run=True` on non-stdio transports. In practice agents and operators forget to run it, and caches accumulate indefinitely.

### Memory amplification

`lgrep` worker holds every queried project index in memory (LanceDB connection + FTS index + vector cache per project). When a multi-session OpenCode setup queries 5 different worktrees of the same repo, the worker loads 5 nearly-identical indexes, each with its own LanceDB connection. RSS scales linearly with worktree count, not with repo count.

## Goals (success criteria)

- Two ADV worktrees of the same repo can share one semantic index, with cache lookups in either path returning identical results without re-embedding
- Archiving an ADV change cleanly invalidates the worktree's cache footprint on disk via a documented MCP surface
- Orphan caches are detected and reclaimed automatically without requiring an operator to remember to run `prune-orphans`
- Worker memory under realistic multi-worktree usage stays bounded — measured by RSS not growing linearly with worktree count for the same repo
- Token spend on first warmup of a worktree does not duplicate embeddings already paid for at the canonical repo path
- Existing single-project / non-worktree usage is unaffected (no regression in latency, recall, or storage shape)

## Non-goals (out of scope)

- Replacing LanceDB or the Voyage Code 3 embedder
- Redesigning the symbol-index store (`IndexStore` keying stays per-path; symbol indexes are small and cheap to rebuild)
- Network/auth changes to MCP transport
- Indexing changes that affect ranking quality
- File-watcher-driven cross-worktree sync (worktrees may diverge mid-session; we do not promise live coherence between them, only that warmups dedupe)
- Distributed cache across machines

## Affected surfaces

| Module | Change shape |
|---|---|
| `storage/_chunk_store.py` | New canonical-identity resolver (git common-dir → cache key), backward-compatible fallback to path-keyed |
| `storage/__init__.py` | Export the resolver |
| `tools/invalidate_cache.py` | Add worktree-mode flag: invalidate only the worktree's view, leaving canonical cache intact |
| `tools/prune_orphans.py` | Add `prune_worktree_caches()` helper for worktree-specific cleanup; keep existing semantics |
| `server/tools_maintenance.py` | New MCP tool: `invalidate_worktree_cache(path)` callable by ADV during archive |
| `server/lifecycle.py` | Optional: in-memory project dedup (one ProjectState per common-dir, multiple path aliases) |
| `server/bootstrap.py` | Background orphan-sweep on server start (cheap; respects grace window) |
| `cli.py` | New subcommand `lgrep gc` wrapping prune + worktree cleanup, suitable for cron / systemd timer |
| `discovery.py` | No changes — `.gitignore`/`.lgrepignore` already handle worktree metadata correctly |
| Tests | New: worktree-dedup behavior, archive-hook tool shape, background sweep idempotency |
| Docs | `README.md` + `docs/`: worktree workflow guidance; integration note for ADV `adv_worktree_delete` + `adv_change_archive` |

## Design sketch (validated in design gate, not law here)

Canonical identity proposal: when `project_path` is inside a git worktree, resolve to `git rev-parse --git-common-dir`, strip trailing `.git`, and use that as the cache key. Fall back to resolved absolute path when the path is not under git control (existing behavior preserved). This is a structural fix (P33) — the cache key becomes a function of repo identity, not filesystem materialization.

Worktree dedup is opt-in via a project-config or env flag for v1, so existing single-checkout users get no behavior change. Default-on can come in a follow-up after telemetry confirms safety.

ADV integration is via two new MCP tools on the lgrep side:
- `invalidate_worktree_cache(worktree_path)` — called by ADV during `/adv-archive` Phase 9 before `adv_worktree_delete`
- `lgrep gc --execute` CLI — suitable for `~/.config/systemd/user/lgrep-gc.timer` weekly sweep

Background sweep policy: on server start, schedule a one-shot `prune_orphans(dry_run=False)` after a 5-minute warmup. Respects the existing 1-hour grace window, so in-flight indexers are never raced. Logged via structlog at info level.

## Acceptance criteria

1. Indexing the same repo at two different worktree paths produces one LanceDB cache directory (when worktree-dedup is enabled), and `lgrep_search_semantic` from either path returns identical results
2. Token-cost telemetry confirms zero re-embed when the second worktree is first queried (cache hit on canonical key)
3. A new MCP tool `invalidate_worktree_cache(path)` returns a structured `WorktreeInvalidationResult` (paths cleaned, bytes reclaimed, errors) and refuses paths outside `LGREP_CACHE_DIR`
4. After `/adv-archive` completes and calls the new MCP tool, the worktree's cache footprint on disk is 0 bytes; the canonical cache remains intact
5. Server startup performs a non-blocking orphan sweep that reclaims `project_path_enoent` and `missing_meta` orphans without affecting active in-memory projects
6. `lgrep gc` CLI subcommand wraps both `prune-orphans --execute` and `prune-worktree-caches`, suitable for unattended scheduled execution
7. Existing tests in `test_prune_orphans.py`, `test_server.py`, `test_symbol_tools.py`, `test_e2e_symbols.py` continue to pass unchanged
8. New tests cover: canonical-key resolution for git worktrees, fallback to path-key for non-git paths, dedup on by-flag, archive-hook tool shape and security guards, background sweep with active projects skipped
9. Documented integration in `README.md` for ADV users; documented `lgrep gc` for operators; documented opt-in flag for worktree-dedup
10. No regression: single-repo non-worktree usage has identical cache layout, identical token cost, identical search latency vs current main

## Risks

| Risk | Mitigation |
|---|---|
| Worktree A and B diverge mid-session (e.g., B has uncommitted edits); canonical cache reflects only one | Document clearly: worktree-dedup assumes worktrees mostly track committed content; uncommitted divergence is handled by existing staleness detection on next query |
| `git rev-parse --git-common-dir` fork cost on every cache lookup | Resolve once per project init, cache the result on `ProjectState` |
| ADV not calling the new MCP tool consistently | Background sweep on server start catches the leak even without archive cooperation |
| Background sweep races a long-running indexer in another process | Existing grace-window mechanism (1h default) already addresses this; reuse without modification |
| Opt-in flag forgotten by users → no observed benefit | After v1 ships, gather telemetry; promote to default-on in a follow-up if no issues surface |
| Path-confinement bypass via canonical-key resolver returning paths outside cache root | All cache writes still go through `get_project_db_path()` which contains them under `LGREP_CACHE_DIR`; common-dir resolution only changes the input hash, not the output directory |

## Alternatives considered

- **Per-worktree caches with manual GC only (current state):** continues to leak disk, duplicate embeddings, and amplify memory. Rejected — that's what we have today.
- **Symlink the worktree cache to the trunk cache:** violates the global "no symlink hacks" rule (`~/.config/opencode/instructions/architecture-discipline.md`). Symlinks would hide the structural problem, break under `realpath` callers, and fail audits.
- **Disable indexing entirely for paths under `~/.local/share/opencode/worktree/`:** opaque to lgrep, requires ADV-specific path knowledge baked into a general-purpose tool. Wrong layer.
- **Index only the git common-dir, never the worktree:** simpler but breaks the case where a user genuinely wants worktree-isolated index (e.g., diverged long-running branch). Opt-in dedup preserves both options.

## Open questions for /adv-clarify (if needed)

1. Should worktree-dedup be opt-in (project-config flag) or opt-out (default-on with override) in v1? Recommendation: opt-in for v1, default-on in v1.x after telemetry.
2. Should `invalidate_worktree_cache` accept a list of paths for batch cleanup, or one path per call? Recommendation: list, matches ADV's pattern of operating on N worktrees at once.
3. Should background sweep run on every server start, or only when last-sweep timestamp is older than threshold? Recommendation: every start with the existing grace window — start-up is rare enough that the overhead is negligible.
4. Should lgrep ship a systemd timer unit alongside the `gc` CLI subcommand, or leave timer setup to the user? Recommendation: document, do not ship — installer footprint stays small.

## Related prior work

- v2.x `lgrep.tools.prune_orphans` already provides the destructive primitive with full safety guards. This change builds on it, does not replace it.
- v2.x `discover_cached_projects()` already filters out cache dirs whose `project_path` no longer exists — the detection is solved, only the GC trigger surface is missing.
- Archived ADV change `2026-05-11-improveLgrepStaleIndex` addressed in-place staleness (file edits inside one worktree); this change is orthogonal — it addresses cross-worktree duplication and lifecycle, not staleness within a single worktree.

## Out of session: implementation hint, not law

A reasonable first slice:

1. Add `canonical_repo_key()` in `storage/_chunk_store.py` that returns `git common-dir` or falls back to `resolve()`, gated behind an env var (`LGREP_WORKTREE_DEDUP=1`)
2. Add `invalidate_worktree_cache` MCP tool + CLI subcommand `lgrep gc`
3. Add background sweep in `server/bootstrap.py`
4. Write integration doc + ADV-side example showing the archive hook
5. Defer in-memory project dedup (lifecycle change) to a follow-up — disk-level dedup alone reclaims the dominant cost