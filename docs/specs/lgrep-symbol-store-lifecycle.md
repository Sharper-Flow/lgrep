# Lgrep Symbol Store Lifecycle

> **Version:** 1.1.0
> **Updated:** 2026-08-07

## Purpose

Capability: Lgrep Symbol Store Lifecycle

## Requirements

### Stale index classification by filesystem existence

**ID:** `rq-V7kP9mNx4hD` | **Priority:** **[MUST]**

When `find_stale_indexes` scans the symbol store, each `index_*.json` file MUST be classified into exactly one of three stable reasons: `repo_path_enoent` (the `repo_path` field references a path that does not exist on disk), `unreadable_index_json` (the file exists but is not valid JSON), or `missing_repo_path_field` (the file is valid JSON but lacks a `repo_path` key). A file whose `repo_path` references an existing directory MUST NOT be classified as stale. Transient filesystem errors (e.g., `PermissionError` during `is_dir()`) MUST be treated as 'preserve, do not classify' rather than as stale.

Sidecar exception: when an index has a key-verified metadata sidecar (`index_<key>.meta.json` whose `repo_path` normalizes to the same index key), classification MUST use the sidecar's `repo_path` without parsing the index body. The three-reason rule above applies unchanged to sidecar-less indexes. Consequence (documented blind spot): an index whose body is not valid JSON but which has a key-verified sidecar is classified from the sidecar rather than as `unreadable_index_json`. This is accepted because torn writes are prevented at the write path (unique temp + atomic rename), out-of-band corruption is the only remaining cause, and such an index self-heals on the next re-index.

#### Scenarios

**repo_path field references deleted path** (`rq-V7kP9mNx4hD.1`)

**Given:**
- a symbol index file `index_abc.json` exists in the storage directory
- the file is valid JSON with `{"repo_path": "/tmp/deleted-repo", ...}`
- the directory `/tmp/deleted-repo` does not exist on disk

**When:** find_stale_indexes scans the storage directory

**Then:**
- the file is classified with reason `repo_path_enoent`
- the file appears in the returned stale list

**Malformed JSON in index file** (`rq-V7kP9mNx4hD.2`)

**Given:**
- a symbol index file `index_xyz.json` exists
- the file contents are not valid JSON (truncated or malformed)

**When:** find_stale_indexes scans the storage directory

**Then:**
- the file is classified with reason `unreadable_index_json`
- the file appears in the returned stale list

**Valid JSON without repo_path key** (`rq-V7kP9mNx4hD.3`)

**Given:**
- a symbol index file `index_def.json` exists
- the file is valid JSON but lacks a `repo_path` key

**When:** find_stale_indexes scans the storage directory

**Then:**
- the file is classified with reason `missing_repo_path_field`
- the file appears in the returned stale list

**Healthy index not classified as stale** (`rq-V7kP9mNx4hD.4`)

**Given:**
- a symbol index file `index_ok.json` exists
- the file is valid JSON with `repo_path: /existing/repo`
- the directory `/existing/repo` exists on disk

**When:** find_stale_indexes scans the storage directory

**Then:**
- the file is NOT classified as stale
- the file does not appear in the returned stale list

---

### Delete-time guards: path-confinement, TOCTOU, grace window, batch isolation

**ID:** `rq-5gL9xM2vHz` | **Priority:** **[MUST]**

When `dry_run=False`, `prune_symbols` MUST enforce four guards: (1) path-confinement — the resolved path of each index file MUST be strictly under the resolved storage root; tampered paths outside the root MUST be refused and recorded in `failures[]`; (2) TOCTOU — symlinks MUST be refused at scan time (skip silently) and at delete time (record in `failures[]`); (3) grace window — entries whose mtime is within `LGREP_PRUNE_MIN_AGE_S` seconds (default 3600, overridable via env var) AND whose reason is `unreadable_index_json` MUST be preserved; reasons `repo_path_enoent` and `missing_repo_path_field` bypass the grace check (they are unambiguous); (4) batch isolation — per-entry `unlink` failures MUST be captured in `failures[]` with the file path and error message, and MUST NOT abort processing of remaining entries.

#### Scenarios

**Path-confinement refuses out-of-root paths at delete time** (`rq-5gL9xM2vHz.1`)

**Given:**
- a stale index whose resolved path resolves to `/etc/passwd` (outside storage root)

**When:** prune_symbols(dry_run=False) processes the entry

**Then:**
- the entry is refused
- the entry appears in `failures[]` with an explanatory error
- the file `/etc/passwd` is not deleted

**TOCTOU refuses symlinks at delete time** (`rq-5gL9xM2vHz.2`)

**Given:**
- a stale index whose file is a symlink to another location

**When:** prune_symbols(dry_run=False) processes the entry

**Then:**
- the entry is refused
- the entry appears in `failures[]`
- the symlink target is not deleted

**Grace window preserves recent unreadable_index_json** (`rq-5gL9xM2vHz.3`)

**Given:**
- a stale index with reason `unreadable_index_json`
- the file's mtime is 60 seconds ago
- LGREP_PRUNE_MIN_AGE_S is set to 3600 (default)

**When:** prune_symbols(dry_run=False) processes the entry

**Then:**
- the entry is NOT deleted
- the entry does not appear in `deleted_dirs` (or equivalent count field)
- the entry is noted as grace-preserved in the report

**Per-entry unlink failure does not abort batch** (`rq-5gL9xM2vHz.4`)

**Given:**
- three stale index files in the storage directory
- the second file raises `OSError` on `unlink()`

**When:** prune_symbols(dry_run=False) processes the batch

**Then:**
- the first file is deleted
- the second file appears in `failures[]` with the OSError message
- the third file is deleted (processing continues)

---

### Sidecar and temp-file reclamation

**ID:** `rq-4sDc7mW2pQ` | **Priority:** **[MUST]**

`save()` writes each index as `index_<key>.json` plus an advisory metadata sidecar `index_<key>.meta.json`. The execute path MUST extend the same four delete-time guards (path-confinement, TOCTOU symlink refusal, grace window, batch isolation) to every file it removes. Deleting a stale index MUST also delete its companion sidecar in the same run. An orphan sidecar (no matching `index_<key>.json`) MUST be reclaimed only past the grace window and MUST be preserved when its repo belongs to the active set; at delete time the entry MUST be re-checked — if the matching index file now exists (recreated between scan and delete) the sidecar MUST be preserved, and an `OSError` during the re-check MUST be recorded in `failures[]` with the sidecar preserved. Stale temp files — legacy `index_<key>.tmp` and writer-unique `index_<key>.<pid>.<rand>.tmp` left by interrupted writes — MUST be reclaimed past the grace window. Lock files (`.index_<key>.lock`) MUST NOT be reclaimed: unlinking an inode another lock waiter holds silently breaks mutual exclusion.

#### Scenarios

**Stale index drags its companion sidecar** (`rq-4sDc7mW2pQ.1`)

**Given:**
- a stale index `index_abc.json` classified with reason `repo_path_enoent`
- its companion sidecar `index_abc.meta.json` exists

**When:** prune_symbols(dry_run=False) processes the entry

**Then:**
- the index is deleted
- the companion sidecar is deleted through the same guarded path
- both sizes count toward `reclaimed_bytes`

**Orphan sidecar of a recreated index is preserved at delete time** (`rq-4sDc7mW2pQ.2`)

**Given:**
- an orphan sidecar `index_def.meta.json` older than the grace window
- the matching `index_def.json` is recreated between scan and delete

**When:** prune_symbols(dry_run=False) processes the entry

**Then:**
- the sidecar is NOT deleted
- the entry does not appear in `failures[]`

**Lock file never reclaimed** (`rq-4sDc7mW2pQ.3`)

**Given:**
- a lock file `.index_abc.lock` older than the grace window
- no matching index file exists

**When:** prune_symbols(dry_run=False) completes

**Then:**
- the lock file remains on disk
- the lock file appears nowhere in the deletion report

---

### lgrep gc umbrella invokes prune_symbols and nests result in combined report

**ID:** `rq-3dV8kP6rBx` | **Priority:** **[MUST]**

The `lgrep gc` command MUST invoke `prune_symbols` alongside the existing `prune_orphans` and `gc_worktree_meta` sweeps. The combined report dict MUST nest the prune_symbols result under a new top-level key spelled exactly `prune_symbols` (snake_case, matching the existing `prune_orphans` and `gc_worktree_meta` key convention). Existing report keys (`prune_orphans`, `gc_worktree_meta`) MUST be preserved with their respective shapes and values unchanged.

#### Scenarios

**gc combined report nests prune_symbols alongside existing keys** (`rq-3dV8kP6rBx.1`)

**Given:**
- the `lgrep gc` command is invoked with default flags

**When:** lgrep gc completes its sweep

**Then:**
- the combined report contains exactly the top-level keys `prune_orphans`, `gc_worktree_meta`, and `prune_symbols`
- the `prune_orphans` and `gc_worktree_meta` values match what those functions return when invoked standalone
- the `prune_symbols` value matches what `prune_symbols(...)` returns when invoked standalone with equivalent arguments

---

### Dry-run default on every surface with reclaimed_bytes projection

**ID:** `rq-2bF6tR8nKp` | **Priority:** **[MUST]**

The core `prune_symbols(...)` function and every operator-facing surface (CLI subcommand `lgrep prune-symbols`, MCP tool `prune_symbols`) MUST default to `dry_run=True` when no explicit argument is provided. The dry-run response MUST include a `reclaimed_bytes` field equal to the projected sum of stale entry file sizes (each `Path(entry).stat().st_size`). The CLI MUST treat `--execute` and `--dry-run` as mutually exclusive flags and exit non-zero with a stderr message when both are passed.

#### Scenarios

**Default invocation preserves disk and reports projected bytes** (`rq-2bF6tR8nKp.1`)

**Given:**
- a storage directory with one stale index file of size 4096 bytes

**When:** prune_symbols() is called with no arguments

**Then:**
- no files are deleted from the storage directory
- the returned report has `dry_run: true`
- the returned report has `reclaimed_bytes: 4096`

**CLI rejects both --execute and --dry-run** (`rq-2bF6tR8nKp.2`)

**Given:**
- the CLI subcommand `lgrep prune-symbols`

**When:** invoked with both `--execute` and `--dry-run` flags simultaneously

**Then:**
- the process exits with a non-zero status code
- an error message is written to stderr explaining the mutual exclusion

---

### Non-local (github:) entries skipped upfront

**ID:** `rq-8sJ3vQ1wYz` | **Priority:** **[MUST]**

Entries whose `repo_path` field starts with the literal prefix `github:` MUST be skipped by `find_stale_indexes` before any stale classification logic runs. They MUST NOT appear in the returned stale list and MUST NOT be deleted by the execute path. These entries represent non-local indexes (e.g., `github:owner/name@ref`) with no local filesystem path to staleness-check.

#### Scenarios

**github: prefix skipped entirely** (`rq-8sJ3vQ1wYz.1`)

**Given:**
- a symbol index file with `repo_path: github:owner/name@ref`
- the file is otherwise valid

**When:** find_stale_indexes scans the storage directory

**Then:**
- the file is NOT in the returned stale list
- the file is preserved on disk after prune_symbols(dry_run=False) returns

---

### MCP destructive safety: deletion requires an explicit capability grant

**ID:** `rq-7cT4wN1qJs` | **Priority:** **[MUST]**

The MCP `prune_symbols` tool MUST default to preview-only and MUST delete in exactly one condition: the explicit out-of-band capability grant `LGREP_ALLOW_DESTRUCTIVE_MCP` is present in the server environment. Transport kind MUST NOT participate in the authority decision, because a proxy can front a local stdio pipe with a shared network surface and the subprocess still reports `stdio`. When the grant is absent the tool MUST return a preview whose `refused_reason` names both the grant and the CLI equivalent (`lgrep prune-symbols --execute`), mirroring the `prune_orphans` convention. The CLI path is unaffected.

Supersedes the earlier transport-inference rule, which granted destructive rights by default in precisely the deployment that most needed protection.

#### Scenarios

**Absent grant yields a preview even on stdio** (`rq-7cT4wN1qJs.1`)

**Given:**
- an MCP request invokes `prune_symbols` with `dry_run: false`
- the transport is stdio
- `LGREP_ALLOW_DESTRUCTIVE_MCP` is not set

**When:** the MCP handler processes the request

**Then:**
- the handler coerces `dry_run: true`
- no files are deleted
- `refused_reason` names the grant and the CLI equivalent

**Present grant honours the caller's request** (`rq-7cT4wN1qJs.2`)

**Given:**
- an MCP request invokes `prune_symbols` with `dry_run: false`
- `LGREP_ALLOW_DESTRUCTIVE_MCP` is set

**When:** the MCP handler processes the request

**Then:**
- the request proceeds with `dry_run: false`
- destructive deletion is allowed
- `refused_reason` is absent

---
