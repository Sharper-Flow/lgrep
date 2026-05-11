# Design

## Architecture Overview

Five surgical fixes across four files, all reusing existing primitives. No new modules, no new dependencies. Each fix has narrow blast radius and a corresponding regression test.

```
┌─ Surface fixes (response-shape) ────────────────────────────────────┐
│  src/lgrep/server/lifecycle.py:_get_project_stats()                 │
│    → add disk_cache=None, error=None on success branch              │
│  src/lgrep/server/responses.py:StatusSemanticResult                 │
│    → no schema change (contract already correct; producer drifted)  │
└─────────────────────────────────────────────────────────────────────┘
┌─ Behavior fixes (staleness) ────────────────────────────────────────┐
│  src/lgrep/tools/index_folder.py                                    │
│    → collect walked_files in existing loop; call detect_changes()   │
│      to prune deleted files/symbols before store.save()             │
│  src/lgrep/server/tools_semantic.py:_execute_search()               │
│    → mtime-gate pre-flight: if any file's mtime > last_index_at,    │
│      hash-check via new ChunkStore.get_file_hashes() projection;    │
│      trigger index_all() on mismatch                                │
└─────────────────────────────────────────────────────────────────────┘
┌─ Hygiene + docs ────────────────────────────────────────────────────┐
│  src/lgrep/discovery.py:DEFAULT_LGREPIGNORE_TEMPLATE                │
│    → append .adv/changes/ and .adv/archive/                         │
│  README.md, skills/lgrep/SKILL.md                                   │
│    → stale-index recovery section                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Decisions

### D1 — `_get_project_stats()` returns complete dict (AC1)
**Problem:** Single-project path constructs `StatusSemanticResult` with all 6 fields. All-projects path returns `_get_project_stats()` output directly, which omits `disk_cache` and `error` on the success branch.

**Fix:** Add `disk_cache: None` and `error: None` to the success-branch dict in `_get_project_stats()` (lifecycle.py:227-232). Error branch already includes `error: str(e)`; add `disk_cache: None` there too.

**Why this is structural (P33):** TypedDict already declares the required shape (`StatusSemanticResult`). The bug is producer drift, not contract drift. Fix is producer-side, two-line addition.

### D2 — Wire `IndexStore.detect_changes()` into `index_folder()` (AC2, UD3)
**Problem:** `index_folder()` builds `files_dict` starting from `existing_files`, iterates current files, and never removes paths absent from current discovery.

**Fix (validator-clarified integration):**
1. The existing loop already computes `rel_path` and `file_hash` per current file (index_folder.py:87-88). Collect them into a `walked_files: dict[str, str]` as the loop runs.
2. After the loop completes, call `store.detect_changes(resolved_root, walked_files)` to obtain `{new, changed, deleted}`.
3. Prune `files_dict` and `symbols_dict` of entries whose `file_path` is in `deleted` before `store.save()`.
4. Add `files_deleted: int` to the result.

**No double-walk.** Per validator note: hashes computed in existing loop are reused.

**Why reuse over inline:** `detect_changes()` is tested (`tests/test_index_store.py:TestIncrementalChangeDetection`). UD3 chose this explicitly.

### D3 — Pre-flight staleness check in `_execute_search()` (AC4, UD2, AD2)
**Problem:** Semantic index can drift from disk; agents currently get stale results unless they manually re-index.

**Fix — three-stage gating to honor 100ms budget:**

**Stage 1 — mtime gate (cheap, always-on):**
- Run `discovery.find_files()` (already-filtered iterator).
- For each current file, compare `file.stat().st_mtime` against the index's `indexed_at` timestamp (already stored on each chunk).
- We track per-project `latest_indexed_at = max(chunk.indexed_at for chunk in table)`. Cache this on `ProjectState` after each `index_all()`; invalidate on file changes.
- If no file's mtime exceeds `latest_indexed_at` AND `len(current_files) == len(indexed_files)`, **assume fresh — skip to embedding query.**

**Stage 2 — hash check (only if mtime-gate suspects drift):**
- Compute SHA-256 for files newer than `latest_indexed_at` only (subset, not full repo).
- Call new `ChunkStore.get_file_hashes() -> dict[str, str]` — single LanceDB projection query using the proven `get_indexed_files()` pattern.
- Compare per-file hashes for the suspect subset.

**Stage 3 — re-index (only on confirmed mismatch):**
- Trigger `indexer.index_all()` via `_auto_index_project_single_flight` (reuses existing leader/follower coordination — validator-confirmed pattern).

**"Warm path" defined:** Stage-1 finds no suspect files → no hashing, no re-index. This is the common case (no edits since last index) and must fit in 100ms. With 57 files: `find_files` ~5ms + 57 `stat()` calls ~1ms = ~6ms. Well within budget.

**Cold path (suspect files exist):** Stage-2 hashes only the suspect subset, not the whole repo. Stage-3 re-index is bounded by Voyage embedding cost (not in 100ms budget — AC4 says "warm-path" only).

**No env var bypass.** Validator caution-3 resolved: removing `LGREP_DISABLE_STALENESS_CHECK`. Stage-1 mtime gate is cheap enough that bypass is unnecessary. If future profiling reveals issues, add bypass in a follow-up change.

### D4 — Default ignore template additions (AC3, UD1)
**Problem:** `.adv/changes/` and `.adv/archive/` markdown is indexed and surfaces stale proposal text in search results.

**Fix:** Append to `DEFAULT_LGREPIGNORE_TEMPLATE` in `discovery.py:29-61`:
```
# ADV change/archive state (stale planning text)
.adv/changes/
.adv/archive/
```

**Out of scope:** Validator confirmed `_SKIP_DIRS` (discovery.py:67-95) is hardcoded; not touched. Template is user-facing scaffold per `scaffold_lgrepignore()`.

**Constraint compliance:** Existing `.lgrepignore` files are never overwritten — `scaffold_lgrepignore()` already enforces `force=False` (discovery.py:200-201). Validator confirmed.

### D5 — Doc updates (AC5)
- **README.md** "Stale semantic results" troubleshooting → auto-staleness check now runs on search; `lgrep_index_semantic` only needed for first-time setup or explicit refresh.
- **skills/lgrep/SKILL.md** → add "Staleness handling" section.
- **instructions/lgrep-tools.md** → no change (policy unchanged).

## Implementation Strategy

Sequenced for TDD red/green per task:

| Step | File | TDD | Depends on |
|---|---|---|---|
| 1 | `tests/test_storage.py` + `_chunk_store.py` — add `get_file_hashes()` batch | red→green | — |
| 2 | `tests/test_indexing.py` — track `latest_indexed_at` on ChunkStore | red→green | step 1 |
| 3 | `tests/test_server_tools.py` or `test_renames.py` — staleness pre-flight test (fresh + stale) | red | step 2 |
| 4 | `tools_semantic.py:_execute_search()` — wire 3-stage pre-flight | green | step 3 |
| 5 | `tests/test_server.py` — all-projects status shape test | red | — |
| 6 | `lifecycle.py:_get_project_stats()` — add fields | green | step 5 |
| 7 | `tests/test_symbol_tools.py` — deleted-file regression test | red | — |
| 8 | `tools/index_folder.py` — wire `detect_changes()` | green | step 7 |
| 9 | `tests/test_discovery.py` — assert new ignore entries in template | red→green | — |
| 10 | `discovery.py` — template additions | covered by 9 | — |
| 11 | Docs: README, SKILL.md | n/a (doc-only) | — |
| 12 | Full `pytest` run | verify | all |

Independent groups: (1-4), (5-6), (7-8), (9-10), (11). Easiest order is as listed.

## LBP Analysis

| Choice | LBP? | Rationale |
|---|---|---|
| Hash-based staleness (SHA-256) with mtime gate | ✓ | Standard for local index tools (ripgrep-all, sourcegraph zoekt, tantivy). mtime gate is universal (git, make, ninja). |
| Reuse `detect_changes()` | ✓ | Tested, lives in same module. P22 (modularity), P19 (simplicity). |
| Pre-flight check vs watcher | ✓ for this case | Watchers add daemon lifecycle complexity. Request-scoped pre-flight matches user mental model ("search returns fresh results"). Watcher still available via `LGREP_AUTO_WATCH=true` for proactive use. |
| Producer-side `_get_project_stats` fix | ✓ | Per P33 (structural correctness): TypedDict is the contract; fix the producer. |
| Always-on staleness (no env-var bypass) | ✓ | Cheap mtime-first gating eliminates need for opt-out. Avoids env-var proliferation. P19 simplicity. |
| Append to default template | ✓ | Non-breaking. Existing files preserved per P01. |

## Affected Components

| File | Change | Lines (approx) |
|---|---|---|
| `src/lgrep/storage/_chunk_store.py` | + `get_file_hashes()` projection query method; + `get_latest_indexed_at()` | +25 |
| `src/lgrep/server/lifecycle.py` | `_get_project_stats()` add 2 fields (success branch); cache `latest_indexed_at` on `ProjectState` | +6 |
| `src/lgrep/server/tools_semantic.py` | `_execute_search()` 3-stage pre-flight | +40 |
| `src/lgrep/tools/index_folder.py` | collect `walked_files`, call `detect_changes()`, prune `deleted` | +15 |
| `src/lgrep/discovery.py` | template additions | +4 |
| `tests/test_storage.py` | + `get_file_hashes` test | +20 |
| `tests/test_indexing.py` | + `latest_indexed_at` tracking test | +20 |
| `tests/test_server_tools.py` | + pre-flight staleness test | +40 |
| `tests/test_server.py` | + all-projects shape test | +25 |
| `tests/test_symbol_tools.py` | + deleted-file regression | +30 |
| `tests/test_discovery.py` | + template content assertion | +5 |
| `README.md` | troubleshooting update | +8 |
| `skills/lgrep/SKILL.md` | staleness section | +12 |

Total: ~250 lines, ~60% tests.

## Validator Result

**Verdict:** CAUTION (3 items, all resolved inline)

| # | Finding | Resolution |
|---|---|---|
| 1 | D3 "warm path" undefined; 100ms budget unproven for large repos | Added 3-stage gating: mtime-first (~6ms typical), hash-only-on-suspicion, re-index-only-on-mismatch. "Warm path" defined as Stage-1 clean exit. |
| 2 | Env var `LGREP_DISABLE_STALENESS_CHECK` ambiguous re: avoidance | Removed bypass. Always-on (Stage-1 mtime gate is cheap enough). |
| 3 | D2 walking files twice unclear | Clarified: existing loop already computes hashes; collect into `walked_files` dict and reuse for `detect_changes()` post-loop. No double-walk. |

**Spec-law compliance:** No conflicts with `lgrepSemanticCacheLifecycle` or `lgrepToolSelectionOptimization`. D1 producer fix directly supports rq-out-1 (typed responses). D3 wraps in existing `_auto_index_project_single_flight` per rq-3 (auto-setup single-flight).

## Risks / Mitigations

| Risk | Mitigation |
|---|---|
| Stage-1 mtime gate misses changes if mtime preserved (rare: `git checkout` with `--no-stat`, rsync `--times`) | Stage-1 also compares `len(current_files) == len(indexed_files)` — catches additions/deletions even with preserved mtimes. Modified-in-place with preserved mtime is the only gap; acceptable since git tooling rarely preserves mtime by default. |
| `find_files()` walk on every search is expensive on huge monorepos (10k+ files) | Mtime gate's `find_files + stat` scales linearly. Bench on representative repo; if needed, add caching keyed on directory mtime in a follow-up. |
| Cached `latest_indexed_at` drifts if `index_all()` runs externally | Recompute on cache miss; invalidate on every `index_file()` / `delete_by_file()` call. |
| Concurrent searches both detect stale → both call `index_all()` | Existing `_auto_index_project_single_flight` (`_indexing_events`) handles this. Validator confirmed. |
| Stage-3 re-index could be expensive (Voyage API cost) on stale projects | Out of warm-path 100ms budget per AC4. Re-index cost is unchanged from manual `lgrep_index_semantic`. |
| Adding `.adv/changes/` ignore in default template affects future `lgrep init-ignore` runs only | By design — existing `.lgrepignore` files preserved. Document in CHANGELOG. |