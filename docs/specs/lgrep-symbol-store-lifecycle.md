# Lgrep Symbol Store Lifecycle

> **Version:** 1.0.0
> **Updated:** 2026-07-21

## Purpose

Capability: Lgrep Symbol Store Lifecycle

## Requirements

### Stale index classification by filesystem existence

**ID:** `rq-V7kP9mNx4hD` | **Priority:** **[MUST]**

When `find_stale_indexes` scans the symbol store, each `index_*.json` file MUST be classified into exactly one of three stable reasons: `repo_path_enoent` (the `repo_path` field references a path that does not exist on disk), `unreadable_index_json` (the file exists but is not valid JSON), or `missing_repo_path_field` (the file is valid JSON but lacks a `repo_path` key). A file whose `repo_path` references an existing directory MUST NOT be classified as stale. Transient filesystem errors (e.g., `PermissionError` during `is_dir()`) MUST be treated as 'preserve, do not classify' rather than as stale.

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

### MCP transport safety: non-stdio coerces dry_run=True

**ID:** `rq-7cT4wN1qJs` | **Priority:** **[MUST]**

The MCP `prune_symbols` tool MUST coerce `dry_run=True` when the MCP transport is not stdio (i.e., HTTP, streamable-http, or unknown). Transport kind is sourced from the application lifespan context. Unknown transport MUST be treated as non-local (defensive default). The tool's user-facing description MUST direct callers to use the CLI (`lgrep prune-symbols --execute`) for destructive prunes on shared deployments, mirroring the `prune_orphans` MCP tool description convention.

#### Scenarios

**stdio transport allows caller-chosen dry_run** (`rq-7cT4wN1qJs.1`)

**Given:**
- an MCP request invokes `prune_symbols` with `dry_run: false`
- the transport is stdio

**When:** the MCP handler processes the request

**Then:**
- the request proceeds with `dry_run: false`
- destructive deletion is allowed

**HTTP transport coerces dry_run=True** (`rq-7cT4wN1qJs.2`)

**Given:**
- an MCP request invokes `prune_symbols` with `dry_run: false`
- the transport is HTTP or streamable-http

**When:** the MCP handler processes the request

**Then:**
- the handler coerces `dry_run: true`
- no files are deleted
- the response indicates the coercion (e.g., `dry_run: true` in the report)

---
