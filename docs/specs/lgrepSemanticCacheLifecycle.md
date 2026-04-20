# LgrepSemanticCacheLifecycle

> **Version:** 1.0.0
> **Updated:** 2026-04-20

## Purpose

Capability: LgrepSemanticCacheLifecycle — defines the invariants and operator-facing surfaces that keep the semantic cache directory (LanceDB chunks + metadata) consistent over time, including orphan detection, manual pruning (CLI + MCP), and lifecycle safety guards.

## Requirements

### Cache directory carries reverse-mapping metadata

**ID:** `rq-cache-meta-invariant` | **Priority:** **[MUST]**

When a `ChunkStore` is constructed with an explicit `project_path`, it MUST write a `project_meta.json` file next to the LanceDB cache on disk. The metadata MUST at minimum record the resolved absolute project path. The write MUST be atomic (write-to-tmp then rename) to avoid partial reads. When `project_path` is omitted, the constructor MUST NOT write meta (writing the cache hash dir as the project path corrupts orphan detection).

**Tags:** `cache`, `storage`, `invariant`

#### Scenarios

**Explicit project_path writes meta** (`rq-cache-meta-invariant.1`)

**Given:**
- A fresh cache directory
- A ChunkStore constructed with a resolved project_path

**When:** Construction completes

**Then:**
- `project_meta.json` exists in the cache directory
- The file's `project_path` field equals the resolved project path
- The write is atomic (no partial file observable by concurrent readers)

**Omitted project_path skips meta** (`rq-cache-meta-invariant.2`)

**Given:**
- A fresh cache directory
- A ChunkStore constructed without a project_path argument

**When:** Construction completes

**Then:**
- No `project_meta.json` is written
- The cache hash dir is never recorded as a project_path

**Corruption recovery rewrites meta** (`rq-cache-meta-invariant.3`)

**Given:**
- A cache directory whose existing LanceDB connection fails and must be re-created
- A ChunkStore reconstructed with project_path after the corruption recovery

**When:** Construction completes

**Then:**
- `project_meta.json` is present after recovery
- Its contents reflect the supplied project_path

---

### Orphan detection classifies four stable reasons

**ID:** `rq-orphan-detection` | **Priority:** **[MUST]**

`find_orphans` MUST classify candidate cache directories using exactly four stable orphan reasons: `missing_meta`, `unreadable_meta`, `missing_chunks_lance`, and `project_path_enoent`. Candidates whose name does not match the semantic cache hash shape (`^[0-9a-f]{12}$`) MUST be skipped outright. Symlinked candidates MUST be refused during scan to prevent any downstream operation from following them. Transient filesystem errors on the project_path side (e.g., `PermissionError` on an unmounted drive) MUST preserve the cache rather than mark it orphan.

**Tags:** `prune-orphans`, `detection`, `security`

#### Scenarios

**Missing meta is detected** (`rq-orphan-detection.1`)

**Given:**
- A cache-shaped directory with chunks.lance/ but no project_meta.json

**When:** find_orphans runs

**Then:**
- The directory is reported with reason `missing_meta`

**Unreadable meta is distinguished from missing** (`rq-orphan-detection.2`)

**Given:**
- A cache-shaped directory whose project_meta.json exists but is not valid JSON

**When:** find_orphans runs

**Then:**
- The directory is reported with reason `unreadable_meta`
- The reported project_path is null

**Missing chunks.lance is detected** (`rq-orphan-detection.3`)

**Given:**
- A cache-shaped directory with a valid project_meta.json but no chunks.lance/ subdir

**When:** find_orphans runs

**Then:**
- The directory is reported with reason `missing_chunks_lance`

**Project-path ENOENT is detected** (`rq-orphan-detection.4`)

**Given:**
- A cache-shaped directory whose project_meta.json points at a project path that no longer exists

**When:** find_orphans runs

**Then:**
- The directory is reported with reason `project_path_enoent`

**Non-hash-shaped children are skipped** (`rq-orphan-detection.5`)

**Given:**
- A cache root containing a child named `symbols/` and a child named `unrelated/`

**When:** find_orphans runs

**Then:**
- Neither child is reported as an orphan
- dirs_examined excludes both because they do not match the cache hash shape

**Symlinked candidates are refused** (`rq-orphan-detection.6`)

**Given:**
- A cache-shaped name that is a symlink to an unrelated directory

**When:** find_orphans or prune_orphans runs

**Then:**
- The symlink is not reported as an orphan
- Its target is not visited or deleted

**Transient PermissionError preserves cache** (`rq-orphan-detection.7`)

**Given:**
- A cache whose project_path raises PermissionError when checked

**When:** find_orphans runs

**Then:**
- The cache is NOT reported as project_path_enoent
- No orphan entry is emitted for it

**Active in-memory projects are skipped** (`rq-orphan-detection.8`)

**Given:**
- A cache-shaped directory whose project path is present in the server's active_set

**When:** prune_orphans runs with that active_set

**Then:**
- The cache is listed under `skipped_active`
- It never appears in `orphans`

---

### Prune is dry-run by default and projects reclaim

**ID:** `rq-prune-dry-run-default` | **Priority:** **[MUST]**

Both the `prune_orphans` core function and every operator-facing surface (CLI, MCP) MUST default to `dry_run=True`. A dry-run response MUST include the projected reclaim (`reclaimed_bytes = sum of orphan bytes`) so operators can preview savings without mutating disk. `--execute` and `--dry-run` MUST be mutually exclusive on the CLI to avoid last-flag-wins surprises.

**Tags:** `prune-orphans`, `safety`, `cli`

#### Scenarios

**Dry-run preserves disk state** (`rq-prune-dry-run-default.1`)

**Given:**
- An orphan cache directory on disk

**When:** prune_orphans runs with dry_run=True (or the CLI default)

**Then:**
- The orphan directory still exists after the call
- deleted_dirs is 0
- reclaimed_bytes equals the sum of orphan[].bytes

**CLI rejects simultaneous --execute and --dry-run** (`rq-prune-dry-run-default.2`)

**Given:**
- A CLI invocation passing both --execute and --dry-run

**When:** the CLI parses arguments

**Then:**
- The command exits with a non-zero status
- stderr explains that --execute and --dry-run are mutually exclusive
- prune_orphans is not invoked

---

### Destructive prune applies path-confinement, TOCTOU, and grace guards

**ID:** `rq-prune-guards` | **Priority:** **[MUST]**

When `dry_run=False`, `prune_orphans` MUST enforce three guards before any `shutil.rmtree` call: (1) path-confinement — the resolved orphan path must be strictly under the resolved cache root; (2) TOCTOU — the orphan path must not be a symlink at delete time; (3) grace — orphan reasons that can be produced by a mid-write indexer (`unreadable_meta`, `missing_chunks_lance`) MUST NOT be reported for caches modified within a grace window (default 3600 seconds, overridable via `LGREP_PRUNE_MIN_AGE_S`; `0` disables). Unambiguous reasons (`missing_meta`, `project_path_enoent`) bypass the grace check. Per-entry failures (rmtree raises, confinement or symlink refusal) MUST be recorded in `failures[]` and MUST NOT abort the batch.

**Tags:** `prune-orphans`, `security`, `toctou`, `path-confinement`

#### Scenarios

**Paths outside the cache root are refused** (`rq-prune-guards.1`)

**Given:**
- A tampered orphans list containing a path outside the resolved cache root

**When:** prune_orphans runs with dry_run=False

**Then:**
- The out-of-root target is not deleted
- failures[] records the refusal with an 'outside cache root' error
- In-scope orphans are still processed

**Symlink orphans are refused at delete time** (`rq-prune-guards.2`)

**Given:**
- A hash-shaped cache entry that is a symlink swapped in after scan

**When:** prune_orphans runs with dry_run=False

**Then:**
- shutil.rmtree is not called on the symlink target
- The symlink may be recorded in failures[] with a 'symlink' error
- The decoy target directory remains intact

**Recently modified ambiguous orphans are held back** (`rq-prune-guards.3`)

**Given:**
- A cache with unreadable_meta whose mtime is within the grace window
- LGREP_PRUNE_MIN_AGE_S > 0

**When:** prune_orphans runs

**Then:**
- The cache is NOT reported as an orphan
- No deletion attempt is made

**Unambiguous reasons bypass grace** (`rq-prune-guards.4`)

**Given:**
- A fresh cache with missing_meta

**When:** prune_orphans runs with grace enabled

**Then:**
- The cache IS reported as missing_meta and eligible for prune

**Per-entry rmtree failures do not abort the batch** (`rq-prune-guards.5`)

**Given:**
- Two orphan cache directories, where shutil.rmtree is patched to fail on the first

**When:** prune_orphans runs with dry_run=False

**Then:**
- The first directory still exists and appears in failures[]
- The second directory is deleted
- deleted_dirs equals 1

---

### MCP prune_orphans coerces dry_run on non-stdio transports

**ID:** `rq-prune-mcp-transport-safety` | **Priority:** **[MUST]**

The MCP `prune_orphans` tool MUST coerce `dry_run=True` whenever the server transport is not `stdio` (or an equivalent local transport). The transport kind MUST be carried on the application context (set from `LGREP_TRANSPORT` at server startup). Unknown or absent transport MUST be treated as non-local. The tool's description MUST tell callers to use the CLI for destructive prunes on shared deployments.

**Tags:** `prune-orphans`, `mcp`, `security`, `transport`

#### Scenarios

**stdio transport allows caller-chosen dry_run** (`rq-prune-mcp-transport-safety.1`)

**Given:**
- An LgrepContext with transport=stdio
- An MCP caller requesting dry_run=False

**When:** the prune_orphans tool runs

**Then:**
- The effective dry_run matches the caller's request

**HTTP transport forces dry_run=True** (`rq-prune-mcp-transport-safety.2`)

**Given:**
- An LgrepContext with transport=streamable-http
- An MCP caller requesting dry_run=False

**When:** the prune_orphans tool runs

**Then:**
- The response's dry_run field is True
- deleted_dirs is 0
- The on-disk orphan still exists

---

### Prune responses use typed dicts aligned with the MCP response pattern

**ID:** `rq-prune-response-contract` | **Priority:** **[MUST]**

The MCP `prune_orphans` tool MUST return a structured dict matching the `PruneOrphansResult` TypedDict, containing `dry_run`, `dirs_examined`, `orphans`, `skipped_active`, `deleted_dirs`, `reclaimed_bytes`, `failures`, and `_meta`. Orphan entries MUST conform to `PruneOrphanEntry` (`path`, `reason`, `bytes`, `project_path`). Failure entries MUST conform to `PruneFailureEntry` (`path`, `error`). This aligns the new MCP surface with the repo-wide typed-response convention.

**Tags:** `prune-orphans`, `mcp`, `output-contract`

#### Scenarios

**MCP dry-run response carries every required key** (`rq-prune-response-contract.1`)

**Given:**
- An MCP prune_orphans call in dry-run mode

**When:** the tool responds

**Then:**
- The response is a dict (not a JSON string)
- The keys {dry_run, dirs_examined, orphans, skipped_active, deleted_dirs, reclaimed_bytes, failures, _meta} are all present
