"""LanceDB storage for lgrep code chunks.

Stores code chunks with embeddings in a local LanceDB database.
Supports hybrid search (vector + FTS) with RRF reranking.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import lancedb
import pyarrow as pa
import structlog
from lancedb.pydantic import LanceModel, Vector
from lancedb.rerankers import RRFReranker
from pydantic import Field

from lgrep.embeddings import MODEL_NAME

if TYPE_CHECKING:
    from lancedb import DBConnection
    from lancedb.table import Table

log = structlog.get_logger()

# Default cache directory
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "lgrep"

# Voyage Code 4 embedding dimensions
EMBEDDING_DIM = 1024

# Table name
CHUNKS_TABLE = "chunks"

# File storing paths known to produce zero chunks, so staleness checks do not
# keep re-attempting them after a complete index window.
_ZERO_CHUNK_FILES_FILENAME = "zero_chunk_files.json"

# File recording completion of the one-time stored line-range repair, so the
# repair pass in Indexer.repair_line_ranges runs once per cache.
_LINE_REPAIR_FILENAME = "line_range_repair.json"

# Rows per merge-insert when rewriting repaired line ranges.
_LINE_REPAIR_BATCH = 10_000

# ``checkout`` value of base rows: the checkout the cache is keyed by. Under
# worktree dedup that is the trunk; a linked worktree stores its overlay rows
# under its resolved root path.
BASE_CHECKOUT = ""
_BASE_WHERE = "checkout = ''"

# Counter increased on every base-row write, so a worktree can tell that the
# base changed since it last compared its files against it.
_BASE_GENERATION_FILENAME = "base_generation.json"

# Directory holding one state file per worktree overlay.
_OVERLAY_DIRNAME = "overlays"
# Scalar indexes that keep the per-checkout search prefilter cheap.
_FILTER_INDEX_COLUMNS = {"checkout": "BITMAP", "file_path": "BTREE"}


def _escape_sql_string(value: str) -> str:
    """Escape a string for use in a LanceDB SQL predicate.

    LanceDB's delete()/where() only accept raw SQL predicate strings, not
    parameterized queries. We escape single quotes using SQL standard doubling
    to prevent injection via crafted values (e.g. file paths containing quotes).
    """
    return value.replace("'", "''")


def _sql_list(values: list[str] | set[str]) -> str:
    """Render strings as the escaped body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{_escape_sql_string(v)}'" for v in sorted(values))


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON through a temporary file and rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.rename(path)


class CodeChunk(LanceModel):
    """LanceDB model for a code chunk.

    Stores code content with embedding vector for semantic search.
    """

    id: str = Field(description="Unique identifier (uuid)")
    file_path: str = Field(description="Relative path from project root")
    chunk_index: int = Field(description="Position of chunk in file")
    start_line: int = Field(description="Starting line number (1-indexed)")
    end_line: int = Field(description="Ending line number (inclusive)")
    content: str = Field(description="Chunk text content")
    vector: Vector(EMBEDDING_DIM) = Field(description="Voyage Code 4 embedding")  # type: ignore[valid-type]
    file_hash: str = Field(description="Hash of source file for invalidation")
    indexed_at: float = Field(description="Unix timestamp of indexing")
    embedding_model: str = Field(
        description="Model that produced the embedding vector",
    )
    checkout: str = Field(
        default=BASE_CHECKOUT,
        description="Checkout the row describes: empty for base rows, else a worktree root",
    )


@dataclass
class SearchResult:
    """A single search result."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float
    match_type: str = "hybrid"


@dataclass
class SearchResults:
    """Results from a search query."""

    results: list[SearchResult] = field(default_factory=list)
    query_time_ms: float = 0.0
    total_chunks: int = 0


def canonical_repo_key(project_path: Path) -> Path:
    """Resolve the canonical cache key for a project path.

    When ``LGREP_WORKTREE_DEDUP`` is enabled and the path is inside a git
    worktree, returns the git common-dir parent (i.e., the repo root).
    Falls back to ``Path.resolve()`` when not under git or when the flag
    is off.

    Uses ``--path-format=absolute`` to guarantee absolute output
    (Git >= 2.30, January 2021).
    """
    resolved = project_path.resolve()

    if not os.environ.get("LGREP_WORKTREE_DEDUP"):
        return resolved

    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            cwd=str(resolved),
            capture_output=True,
            text=True,
            timeout=2,
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
            # Bare repos or unusual layouts — fallback
            return resolved
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return resolved


def get_project_db_path(project_path: str | Path) -> Path:
    """Get the database path for a project.

    Creates a unique path based on the project's canonical key hash.
    When ``LGREP_WORKTREE_DEDUP`` is enabled, git worktrees sharing a
    common ``.git`` directory resolve to the same cache key.

    Args:
        project_path: Path to the project directory

    Returns:
        Path to the project's LanceDB directory
    """
    key = canonical_repo_key(Path(project_path))
    path_hash = hashlib.sha256(str(key).encode()).hexdigest()[:12]

    cache_dir = Path(os.environ.get("LGREP_CACHE_DIR", DEFAULT_CACHE_DIR))
    return cache_dir / path_hash


def checkout_scope(project_path: str | Path) -> tuple[Path, str]:
    """Return ``(cache owner path, checkout id)`` for a project path.

    The cache owner is ``canonical_repo_key``. The checkout id is
    ``BASE_CHECKOUT`` when the path is the owner itself, and the resolved
    path when it is a linked worktree sharing the owner's cache.
    """
    resolved = Path(project_path).resolve()
    owner = canonical_repo_key(resolved)
    return owner, BASE_CHECKOUT if owner == resolved else str(resolved)


def open_checkout_store(project_path: str | Path) -> ChunkStore:
    """Open the store a project path reads and writes.

    Returns the cache owner's store for the owner itself and an
    ``OverlayStore`` view of it for a linked worktree.
    """
    owner, checkout = checkout_scope(project_path)
    store = ChunkStore(get_project_db_path(owner), project_path=owner)
    return store.for_checkout(checkout)


def prune_overlays(
    db_path: str | Path,
    checkouts: set[str] | None = None,
    dry_run: bool = False,
) -> tuple[list[str], int]:
    """Delete worktree overlay rows and state files from one cache.

    With ``checkouts`` given, removes those overlays. Otherwise removes every
    overlay whose worktree directory no longer exists; a directory that
    cannot be checked is kept. Opens the table without migrating it, so a
    cache without the ``checkout`` column has no overlay rows to remove.

    Returns ``(removed checkout ids, rows removed)``; with ``dry_run`` the
    rows are counted and nothing is deleted.
    """
    db_path = Path(db_path)
    overlay_dir = db_path / _OVERLAY_DIRNAME
    state_files: dict[str, Path] = {}
    if overlay_dir.is_dir():
        for state_file in overlay_dir.glob("*.json"):
            try:
                checkout = json.loads(state_file.read_text(encoding="utf-8")).get("checkout")
            except (OSError, json.JSONDecodeError):
                continue
            if checkout:
                state_files[checkout] = state_file

    table = None
    known = set(state_files)
    if (db_path / (CHUNKS_TABLE + ".lance")).is_dir():
        try:
            table = lancedb.connect(str(db_path)).open_table(CHUNKS_TABLE)
        except Exception as e:
            log.warning("prune_overlays_open_failed", db_path=str(db_path), error=str(e))
        if table is not None and "checkout" in table.schema.names:
            overlay_where = f"NOT ({_BASE_WHERE})"
            count = table.count_rows(overlay_where)
            if count:
                column = (
                    table.search().where(overlay_where).select(["checkout"]).limit(count).to_arrow()
                )
                known.update(column.column("checkout").to_pylist())
        else:
            table = None

    if checkouts is not None:
        targets = known & set(checkouts)
    else:
        targets = set()
        for checkout in known:
            try:
                if not Path(checkout).is_dir():
                    targets.add(checkout)
            except OSError:
                continue

    if not targets:
        return [], 0

    where = f"checkout IN ({_sql_list(targets)})"
    rows = table.count_rows(where) if table is not None else 0
    if not dry_run:
        if table is not None and rows:
            table.delete(where)
        for checkout in targets:
            if checkout in state_files:
                state_files[checkout].unlink(missing_ok=True)
        log.info("overlays_pruned", db_path=str(db_path), checkouts=len(targets), rows=rows)
    return sorted(targets), rows


def has_disk_cache(project_path: str | Path) -> bool:
    """Check whether a project has an existing LanceDB index on disk.

    Looks for the ``chunks.lance`` directory inside the project's cache
    directory.  This is a pure filesystem check — it does not open the
    database or require an API key.

    Args:
        project_path: Path to the project directory.

    Returns:
        True if a chunks table exists on disk for this project.
    """
    db_path = get_project_db_path(project_path)
    return (db_path / (CHUNKS_TABLE + ".lance")).is_dir()


# Project metadata — maps cache hash dirs back to original project paths
_META_FILENAME = "project_meta.json"


def write_project_meta(
    project_path: str | Path,
    *,
    db_path: str | Path | None = None,
    alias_paths: list[str] | None = None,
) -> None:
    """Write a metadata file alongside the LanceDB cache for reverse-mapping.

    Stores the original project path so ``discover_cached_projects`` can
    reconstruct which hash dir belongs to which project. The write is
    atomic (write-to-tmp then rename) to avoid partial reads. When
    ``db_path`` is omitted it is derived from ``project_path`` via
    ``get_project_db_path``; callers that already know the cache
    directory (for example ``ChunkStore.__init__``) may pass it directly
    to avoid recomputing the hash.

    ``alias_paths`` records additional filesystem paths (worktree paths)
    that resolve to the same canonical cache.  When provided, the new
    aliases are merged with any existing aliases from a prior write.
    Within a single lgrep MCP process the ``asyncio.Lock`` in
    ``_ensure_project_initialized`` serializes all inits, so no race.
    Across separate processes, the read-modify-write block is guarded by
    ``fcntl.flock`` on ``<cache_dir>/.meta.lock`` (POSIX advisory lock) so
    concurrent multi-process writes do not lose aliases. On platforms
    without ``fcntl`` (Windows), a one-time warning is logged and the
    write proceeds without locking (single-developer / single-process
    deployments stay unaffected).
    """
    project_path = str(Path(project_path).resolve())
    resolved_db_path = Path(db_path) if db_path is not None else get_project_db_path(project_path)
    meta_path = resolved_db_path / _META_FILENAME
    tmp_path = meta_path.with_suffix(".tmp")
    lock_path = resolved_db_path / ".meta.lock"
    resolved_db_path.mkdir(parents=True, exist_ok=True)

    # Acquire POSIX advisory lock on a dedicated lock file. We lock on a
    # SEPARATE file (not the meta itself) because the atomic rename in
    # the write step would otherwise replace the file we hold open,
    # invalidating the lock identity.
    fcntl_mod = None
    lock_fd = None
    try:
        import fcntl as _fcntl

        fcntl_mod = _fcntl
    except ImportError:
        log.warning(
            "fcntl_unavailable_alias_writes_unguarded",
            note="non-POSIX platform; alias_paths writes unguarded across processes",
        )

    if fcntl_mod is not None:
        try:
            # Open with O_CREAT so the lock file is created on first use.
            # Keep open for the duration of the read-modify-write.
            lock_fd = open(lock_path, "a+")  # noqa: SIM115 — closed in finally
            fcntl_mod.flock(lock_fd.fileno(), fcntl_mod.LOCK_EX)
        except OSError:
            # Lock setup failed — proceed unguarded rather than block writes
            log.warning("flock_setup_failed", project=project_path)
            if lock_fd is not None:
                lock_fd.close()
                lock_fd = None

    try:
        # Merge with existing aliases (read-modify-write)
        # — protected by flock above when available. A write without
        # alias_paths keeps the recorded aliases.
        existing_aliases: list[str] = []
        existing_meta = read_project_meta(resolved_db_path)
        if existing_meta and "alias_paths" in existing_meta:
            existing_aliases = list(existing_meta["alias_paths"])
        # Merge: deduplicate while preserving order
        seen = set(existing_aliases)
        for alias in alias_paths or []:
            if alias not in seen:
                existing_aliases.append(alias)
                seen.add(alias)

        payload: dict = {"project_path": project_path, "updated_at": time.time()}
        if existing_aliases:
            payload["alias_paths"] = existing_aliases
        try:
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            tmp_path.rename(meta_path)
        except OSError:
            # Best-effort — never block startup
            log.warning("write_project_meta_failed", project=project_path)
    finally:
        if lock_fd is not None and fcntl_mod is not None:
            with contextlib.suppress(OSError):
                fcntl_mod.flock(lock_fd.fileno(), fcntl_mod.LOCK_UN)
            lock_fd.close()


def read_project_meta(db_path: Path) -> dict | None:
    """Read project metadata from a cache directory.

    Returns the parsed dict or None if the file is missing/corrupt.
    """
    meta_path = db_path / _META_FILENAME
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def discover_cached_projects(max_results: int = 20) -> list[str]:
    """Scan the lgrep cache directory for projects with existing disk caches.

    Returns a list of original project paths, sorted by most recently
    updated (newest first), capped at *max_results*.

    Only returns projects whose directory still exists on the filesystem,
    filtering out stale/deleted projects.
    """
    cache_dir = Path(os.environ.get("LGREP_CACHE_DIR", DEFAULT_CACHE_DIR))
    if not cache_dir.is_dir():
        return []

    entries: list[tuple[float, str]] = []
    try:
        for child in cache_dir.iterdir():
            if not child.is_dir():
                continue
            # Must have a valid LanceDB cache
            if not (child / (CHUNKS_TABLE + ".lance")).is_dir():
                continue
            meta = read_project_meta(child)
            if meta is None:
                continue
            project_path = meta.get("project_path", "")
            if not project_path or not Path(project_path).is_dir():
                continue
            updated_at = meta.get("updated_at", 0.0)
            entries.append((updated_at, project_path))
    except OSError:
        log.warning("discover_cached_projects_scan_failed")
        return []

    # Sort by most recent first
    entries.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in entries[:max_results]]


class ChunkStore:
    """LanceDB-backed storage for code chunks.

    Provides vector and hybrid search over indexed code chunks. A
    ``ChunkStore`` owns the connection and table of one cache and reads and
    writes its base rows (``checkout = BASE_CHECKOUT``). A linked worktree
    sharing the cache uses ``for_checkout`` to get an ``OverlayStore`` view.
    """

    def __init__(self, db_path: str | Path, project_path: str | Path | None = None) -> None:
        """Initialize the chunk store.

        Args:
            db_path: Path to the LanceDB database directory.
            project_path: Original project path for metadata writes. When
                provided, the constructor emits `project_meta.json` next
                to the LanceDB cache so reverse-mapping tools (orphan
                pruning, disk-cache discovery) can recover the mapping.
                Callers that only need temporary stores (tests, tools)
                may pass ``None`` to skip metadata persistence — no
                `project_meta.json` is created in that case. Writing the
                hash dir as project_path would corrupt orphan detection.
        """
        self.db_path = Path(db_path)
        # NOTE: when project_path is None we intentionally keep
        # self._project_path as None so the metadata side-effect is
        # skipped below. Do not fall back to db_path — it would record
        # the hash dir as the project path and confuse prune_orphans.
        self._project_path = Path(project_path).resolve() if project_path is not None else None
        # Row scope of this store and the store that owns the connection and
        # table state; OverlayStore views share the owner's.
        self.checkout = BASE_CHECKOUT
        self._owner = self
        # Completion of the one-time line-range repair; read from disk until true.
        self._line_repair_done = False
        self.db_path.mkdir(parents=True, exist_ok=True)

        try:
            self.db: DBConnection = lancedb.connect(str(self.db_path))
        except Exception as e:
            log.warning(
                "chunk_store_connection_failed",
                db_path=str(self.db_path),
                error=str(e),
                action="clearing and reconnecting",
            )
            # Clear corrupted data and retry
            import shutil

            for item in self.db_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            self.db = lancedb.connect(str(self.db_path))

        self._table: Table | None = None
        self._fts_indexed = False
        self._vector_indexed = False
        self._filter_indexed = False
        self._persist_meta()

        log.info("chunk_store_connected", db_path=str(self.db_path))

    def _persist_meta(self) -> None:
        """Persist project metadata next to the cache directory.

        Skipped when the caller did not supply a project_path — writing
        meta in that case would record the cache hash dir as the
        project, confusing orphan detection and reverse-mapping tools.
        """
        if self._project_path is None:
            return
        write_project_meta(self._project_path, db_path=self.db_path)

    @property
    def table(self) -> Table:
        """Get or create the chunks table."""
        if self._table is None:
            created = False
            try:
                # Use open_table directly (EAFP) — avoids lancedb
                # list_tables() returning ListTablesResponse which
                # breaks `in` operator checks.
                self._table = self.db.open_table(CHUNKS_TABLE)
                log.debug("chunk_table_opened", rows=self._table.count_rows())
                if self._table_needs_rebuild():
                    self.db.drop_table(CHUNKS_TABLE, ignore_missing=True)
                    self._table = self.db.create_table(
                        CHUNKS_TABLE,
                        schema=CodeChunk.to_arrow_schema(),
                    )
                    created = True
                    log.info("chunk_table_recreated_after_model_mismatch")
            except (FileNotFoundError, ValueError) as _not_found:
                # Table doesn't exist yet — normal first-run path
                self._table = self.db.create_table(
                    CHUNKS_TABLE,
                    schema=CodeChunk.to_arrow_schema(),
                )
                created = True
                log.info("chunk_table_created")
            except Exception as open_err:
                # Unexpected error (corruption, permission, etc.)
                log.warning(
                    "chunk_table_open_failed",
                    error=str(open_err),
                    action="dropping and recreating table",
                )
                try:
                    self.db.drop_table(CHUNKS_TABLE, ignore_missing=True)
                except Exception as drop_err:
                    log.debug("drop_table_also_failed", error=str(drop_err))
                self._table = self.db.create_table(
                    CHUNKS_TABLE,
                    schema=CodeChunk.to_arrow_schema(),
                )
                created = True
                log.info("chunk_table_recreated_after_corruption")
            if not created:
                self._add_checkout_column()
                self._probe_existing_indexes()
        return self._table

    def _add_checkout_column(self) -> None:
        """Give a table from before per-checkout rows its ``checkout`` column.

        Every existing row becomes a base row. Only the new column is
        written; content, vectors, and indexes stay as stored.
        """
        if self._table is None or "checkout" in self._table.schema.names:
            return
        self._table.add_columns({"checkout": "''"})
        log.info("chunk_table_checkout_column_added", rows=self._table.count_rows())

    def for_checkout(self, checkout: str) -> ChunkStore:
        """Return the store for ``checkout`` in this cache.

        ``BASE_CHECKOUT`` returns the owning store itself; any other id
        returns an ``OverlayStore`` sharing the owner's table.
        """
        if checkout == BASE_CHECKOUT:
            return self._owner
        return OverlayStore(self._owner, checkout)

    def _own_where(self) -> str:
        """SQL predicate selecting the rows this store writes."""
        return f"checkout = '{_escape_sql_string(self.checkout)}'"

    def _visible_where(self) -> str:
        """SQL predicate selecting the rows this store searches."""
        return self._own_where()

    def _rows_changed(self) -> None:
        """Record a write to this store's rows."""
        self._bump_base_generation()

    def base_generation(self) -> int:
        """Return the base-row write counter of this cache (0 when unset)."""
        try:
            path = self._owner.db_path / _BASE_GENERATION_FILENAME
            if not path.is_file():
                return 0
            return int(json.loads(path.read_text(encoding="utf-8")).get("generation", 0))
        except Exception as e:
            log.debug("base_generation_read_failed", error=str(e))
            return 0

    def _bump_base_generation(self) -> None:
        try:
            _write_json_atomic(
                self._owner.db_path / _BASE_GENERATION_FILENAME,
                {"generation": self.base_generation() + 1},
            )
        except OSError as e:
            log.warning("base_generation_write_failed", error=str(e))

    def needs_full_recheck(self) -> bool:
        """Return whether every file must be re-hashed before trusting mtimes.

        Base rows follow their own checkout, so the base never needs it.
        """
        return False

    def adopt_base_version(self, file_path: str, file_hash: str) -> bool:
        """Serve base rows for a file whose content equals the base version.

        Returns True when the store now serves base rows for ``file_path``
        without new embeddings. The base store has no other version to adopt.
        """
        return False

    def sync_checkout(self, current: dict[str, str], checked_at: float) -> None:
        """Reconcile stored rows with the checkout's current files.

        ``current`` maps every discovered relative path to its sha256.
        Rows of files no longer on disk are deleted. ``checked_at`` is when
        the file walk started; the base store does not need it.
        """
        for stale_path in sorted(self.get_indexed_files() - current.keys()):
            self.delete_by_file(stale_path)
            log.info("stale_file_removed", file=stale_path)

    def _table_needs_rebuild(self) -> bool:
        """Return whether the opened table lacks or uses a stale model schema."""
        if self._table is None:
            return False

        if "embedding_model" not in self._table.schema.names:
            log.info("chunk_table_schema_missing_embedding_model")
            return True

        row_count = self._table.count_rows()
        if row_count == 0:
            return False

        arrow_table = self._table.search().select(["embedding_model"]).limit(row_count).to_arrow()
        models = set(arrow_table.column("embedding_model").to_pylist())
        if models != {MODEL_NAME}:
            log.info(
                "chunk_table_embedding_model_mismatch",
                stored_models=sorted(str(model) for model in models),
                configured_model=MODEL_NAME,
            )
            return True
        return False

    def _probe_existing_indexes(self) -> None:
        """Best-effort probe for indexes persisted by LanceDB.

        LanceDB versions expose index metadata with slightly different object
        shapes, so this method only promotes positive readiness signals. Failure
        leaves both flags unchanged and search still degrades safely to vector.
        """
        if self._table is None or not hasattr(self._table, "list_indices"):
            return
        try:
            indexes = self._table.list_indices()
        except Exception as e:
            log.debug("index_readiness_probe_failed", error=str(e))
            return

        rendered = " ".join(str(index).lower() for index in indexes)
        if "content" in rendered and ("fts" in rendered or "inverted" in rendered):
            self._fts_indexed = True
        if "vector" in rendered or "ivf" in rendered or "hnsw" in rendered:
            self._vector_indexed = True
        indexed_columns = {
            column for index in indexes for column in (getattr(index, "columns", None) or [])
        }
        if set(_FILTER_INDEX_COLUMNS) <= indexed_columns:
            self._filter_indexed = True

    def add_chunks(self, chunks: list[CodeChunk]) -> int:
        """Add chunks to the store.

        Args:
            chunks: List of CodeChunk objects to add

        Returns:
            Number of chunks added
        """
        if not chunks:
            return 0

        # Convert to dicts for LanceDB; rows always belong to this store's checkout.
        data = [{**chunk.model_dump(), "checkout": self.checkout} for chunk in chunks]
        self.table.add(data)
        self._rows_changed()

        log.info("chunks_added", count=len(chunks))
        return len(chunks)

    def upsert_chunks(self, chunks: list[CodeChunk]) -> int:
        """Upsert chunks (update existing, insert new).

        Args:
            chunks: List of CodeChunk objects to upsert

        Returns:
            Number of chunks upserted
        """
        if not chunks:
            return 0

        data = [{**chunk.model_dump(), "checkout": self.checkout} for chunk in chunks]

        # Use merge_insert for upsert
        self.table.merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(data)
        self._rows_changed()

        log.info("chunks_upserted", count=len(chunks))
        return len(chunks)

    def delete_by_file(self, file_path: str) -> int:
        """Delete all chunks for a file.

        Args:
            file_path: Relative path of the file

        Returns:
            Number of chunks deleted
        """
        safe_path = _escape_sql_string(file_path)
        where = f"file_path = '{safe_path}' AND {self._own_where()}"
        deleted = self.table.count_rows(where)
        if deleted:
            self.table.delete(where)
            self._rows_changed()
        log.info("chunks_deleted", file_path=file_path, count=deleted)
        return deleted

    def ensure_fts_index(self) -> None:
        """Ensure the FTS index exists on the content column."""
        if not self._fts_indexed:
            try:
                self.table.create_fts_index("content")
                self._fts_indexed = True
                log.info("fts_index_created")
            except Exception as e:
                log.warning("fts_index_failed", error=str(e))

    def prepare_hybrid_indexes(self, vector_index_row_threshold: int = 1000) -> None:
        """Prepare hybrid-search indexes outside the live query path."""
        self.ensure_fts_index()
        row_count = self.table.count_rows()
        if row_count > vector_index_row_threshold and not self._vector_indexed:
            try:
                self.table.create_index(
                    metric="cosine",
                    vector_column_name="vector",
                )
                self._vector_indexed = True
                log.info("vector_index_created", rows=row_count)
            except Exception as idx_err:
                log.debug("vector_index_create_skipped", error=str(idx_err))
        if row_count > vector_index_row_threshold and not self._filter_indexed:
            # A worktree prefilter excludes its shadowed base paths with
            # ``file_path NOT IN (...)``. Without scalar indexes, hybrid
            # search evaluates that list per row and slows with its length.
            try:
                for column, index_type in _FILTER_INDEX_COLUMNS.items():
                    self.table.create_scalar_index(column, index_type=index_type)
                self._filter_indexed = True
                log.info("filter_indexes_created", rows=row_count)
            except Exception as idx_err:
                log.debug("filter_index_create_skipped", error=str(idx_err))

    def search_hybrid(
        self,
        query_vector: list[float],
        query_text: str,
        limit: int = 10,
    ) -> SearchResults:
        """Perform hybrid search (vector + FTS with RRF reranking).

        Args:
            query_vector: Embedding vector for the query
            query_text: Original query text for FTS
            limit: Maximum results to return

        Returns:
            SearchResults with ranked results
        """
        start = time.perf_counter()

        if not self._owner._fts_indexed:
            log.info(
                "hybrid_search_degraded_to_vector",
                reason="fts_index_not_ready",
                action="prepare_hybrid_indexes_outside_query_path",
            )
            return self.search_vector(query_vector, limit)

        # Hybrid search with RRF reranking. The prefilter applies to both the
        # vector and the full-text side, so only this checkout's rows rank.
        reranker = RRFReranker()
        raw_results = (
            self.table.search(query_type="hybrid")
            .vector(query_vector)
            .text(query_text)
            .where(self._visible_where(), prefilter=True)
            .rerank(reranker)
            .limit(limit)
            .to_list()
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        results = [
            SearchResult(
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                score=r.get("_relevance_score", r.get("_distance", 0.0)),
                match_type="hybrid",
            )
            for r in raw_results
        ]

        return SearchResults(
            results=results,
            query_time_ms=elapsed_ms,
            total_chunks=self.count_chunks(),
        )

    def search_vector(
        self,
        query_vector: list[float],
        limit: int = 10,
    ) -> SearchResults:
        """Perform vector-only search.

        Args:
            query_vector: Embedding vector for the query
            limit: Maximum results to return

        Returns:
            SearchResults with ranked results
        """
        start = time.perf_counter()

        raw_results = (
            self.table.search(query_vector)
            .where(self._visible_where(), prefilter=True)
            .limit(limit)
            .to_list()
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        results = [
            SearchResult(
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                score=r.get("_distance", 0.0),
                match_type="vector",
            )
            for r in raw_results
        ]

        return SearchResults(
            results=results,
            query_time_ms=elapsed_ms,
            total_chunks=self.count_chunks(),
        )

    def count_chunks(self) -> int:
        """Count the chunks this store searches."""
        return self.table.count_rows(self._visible_where())

    def get_file_hash(self, file_path: str) -> str | None:
        """Get the stored hash of a file in this store's own rows, if any."""
        try:
            # Query just one chunk for this file to get its stored hash
            safe_path = _escape_sql_string(file_path)
            results = (
                self.table.search()
                .where(f"file_path = '{safe_path}' AND {self._own_where()}")
                .limit(1)
                .select(["file_hash"])
                .to_list()
            )
            if results:
                return results[0]["file_hash"]
            return None
        except Exception as e:
            log.debug("get_file_hash_failed", file_path=file_path, error=str(e))
            return None

    def get_indexed_files(self) -> set[str]:
        """Get the set of file paths this store serves."""
        return set(self.get_file_hashes())

    def get_file_hashes(self) -> dict[str, str]:
        """Return a mapping of indexed file paths to their stored content hashes.

        Projects only ``file_path`` and ``file_hash`` of this store's rows,
        so vectors are never loaded. Designed for cheap freshness checks:
        callers compare these stored hashes against current on-disk SHA-256
        values to detect drift before paying the cost of a full re-embed.

        When a file has multiple chunks (the common case), the first hash
        encountered is returned — all chunks of a file share the same
        ``file_hash`` value by construction (see ``Indexer.index_file``).
        Returns an empty dict on error or when the store has no rows.
        """
        return self._file_hashes(self._own_where())

    def _file_hashes(self, where: str) -> dict[str, str]:
        try:
            count = self.table.count_rows(where)
            if count == 0:
                return {}
            arrow_table = (
                self.table.search()
                .where(where)
                .select(["file_path", "file_hash"])
                .limit(count)
                .to_arrow()
            )
            paths = arrow_table.column("file_path").to_pylist()
            hashes = arrow_table.column("file_hash").to_pylist()
            result: dict[str, str] = {}
            for path, file_hash in zip(paths, hashes, strict=False):
                if path not in result:
                    result[path] = file_hash
            return result
        except Exception as e:
            log.debug("get_file_hashes_failed", error=str(e))
            return {}

    def get_zero_chunk_files(self) -> set[str]:
        """Return paths known to produce zero chunks.

        Zero-chunk files are not present in the chunks table, so a staleness
        check that simply compares current files to indexed files would flag
        them as pending forever.  This persisted set lets completed index
        windows remember that such files were already considered and can be
        ignored on subsequent checks.
        """
        try:
            path = self.db_path / _ZERO_CHUNK_FILES_FILENAME
            if not path.is_file():
                return set()
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(data.get("files", []))
        except Exception as e:
            log.debug("get_zero_chunk_files_failed", error=str(e))
            return set()

    def add_zero_chunk_files(self, paths: list[str]) -> None:
        """Persist a set of paths that produced zero chunks in this window.

        Merging avoids duplicate entries.  Failures are logged and ignored so
        zero-chunk tracking never blocks indexing.
        """
        if not paths:
            return
        try:
            current = self.get_zero_chunk_files()
            current.update(paths)
            path = self.db_path / _ZERO_CHUNK_FILES_FILENAME
            path.write_text(
                json.dumps({"files": sorted(current)}, indent=2),
                encoding="utf-8",
            )
            log.info("zero_chunk_files_persisted", count=len(current))
        except Exception as e:
            log.warning("add_zero_chunk_files_failed", error=str(e))

    def remove_zero_chunk_file(self, file_path: str) -> None:
        """Remove a path from the zero-chunk set, e.g. after it gains content."""
        try:
            current = self.get_zero_chunk_files()
            if file_path not in current:
                return
            current.discard(file_path)
            path = self.db_path / _ZERO_CHUNK_FILES_FILENAME
            if current:
                path.write_text(
                    json.dumps({"files": sorted(current)}, indent=2),
                    encoding="utf-8",
                )
            else:
                path.unlink(missing_ok=True)
        except Exception as e:
            log.warning("remove_zero_chunk_file_failed", error=str(e))

    def line_repair_done(self) -> bool:
        """Return whether the one-time stored line-range repair completed.

        Search asks on every call, so a true answer is kept in memory and
        the marker file is read only until the repair has completed.
        """
        if self._line_repair_done:
            return True
        try:
            path = self.db_path / _LINE_REPAIR_FILENAME
            if not path.is_file():
                return False
            done = bool(json.loads(path.read_text(encoding="utf-8")).get("done", False))
        except Exception as e:
            log.debug("line_repair_state_read_failed", error=str(e))
            return False
        self._line_repair_done = done
        return done

    def mark_line_repair_done(self) -> None:
        """Record the one-time stored line-range repair as complete."""
        try:
            path = self.db_path / _LINE_REPAIR_FILENAME
            path.write_text(
                json.dumps({"done": True, "completed_at": time.time()}),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("line_repair_state_write_failed", error=str(e))

    def get_line_repair_rows(self) -> dict[str, list[dict]]:
        """Return each file's stored chunks for the line-range repair.

        One projected scan of ``id``, ``file_path``, ``chunk_index``,
        ``content``, ``start_line`` and ``end_line``; vectors are never
        read. Rows are grouped by file and sorted by ``chunk_index``.
        Returns an empty dict when the store has no rows.
        """
        where = self._own_where()
        count = self.table.count_rows(where)
        if count == 0:
            return {}
        columns = ["id", "file_path", "chunk_index", "content", "start_line", "end_line"]
        rows = self.table.search().where(where).select(columns).limit(count).to_arrow().to_pylist()
        by_file: dict[str, list[dict]] = {}
        for row in rows:
            by_file.setdefault(row.pop("file_path"), []).append(row)
        for file_rows in by_file.values():
            file_rows.sort(key=lambda r: r["chunk_index"])
        return by_file

    def update_chunk_line_ranges(self, updates: list[tuple[str, int, int]]) -> int:
        """Set ``start_line``/``end_line`` on stored rows, matched by ``id``.

        ``updates`` holds ``(id, start_line, end_line)`` tuples. The merge
        carries only those three columns, so content and vectors stay as
        stored. Returns the number of rows submitted.
        """
        if not updates:
            return 0
        schema = self.table.schema
        for offset in range(0, len(updates), _LINE_REPAIR_BATCH):
            batch = updates[offset : offset + _LINE_REPAIR_BATCH]
            source = pa.table(
                {
                    "id": pa.array([u[0] for u in batch], type=schema.field("id").type),
                    "start_line": pa.array(
                        [u[1] for u in batch], type=schema.field("start_line").type
                    ),
                    "end_line": pa.array([u[2] for u in batch], type=schema.field("end_line").type),
                }
            )
            self.table.merge_insert("id").when_matched_update_all().execute(source)
        log.info("chunk_line_ranges_updated", count=len(updates))
        return len(updates)

    def get_latest_indexed_at(self) -> float:
        """Return the most-recent ``indexed_at`` timestamp of this store's rows.

        Used as a cheap mtime gate: if no file on disk has been modified after
        this timestamp, the index is fresh and no further checking is needed.
        Returns ``0.0`` when the store has no rows or on error (safe default —
        forces a full check rather than a false-fresh result).
        """
        try:
            where = self._own_where()
            count = self.table.count_rows(where)
            if count == 0:
                return 0.0
            arrow_table = (
                self.table.search().where(where).select(["indexed_at"]).limit(count).to_arrow()
            )
            values = arrow_table.column("indexed_at").to_pylist()
            return float(max(values)) if values else 0.0
        except Exception as e:
            log.debug("get_latest_indexed_at_failed", error=str(e))
            return 0.0

    def clear(self) -> None:
        """Drop the whole cache table, rows of every checkout included."""
        self.db.drop_table(CHUNKS_TABLE, ignore_missing=True)
        self._table = None
        self._fts_indexed = False
        self._vector_indexed = False
        self._filter_indexed = False
        log.info("chunk_store_cleared")


@dataclass
class _OverlayState:
    """Persisted bookkeeping of one worktree overlay.

    ``shadowed`` holds base paths the worktree lacks or holds with other
    content; search hides their base rows. ``base_generation`` and
    ``checked_at`` record the base write counter and the start time of the
    last full comparison of the worktree's files against base.
    """

    shadowed: set[str] = field(default_factory=set)
    zero_chunk_files: set[str] = field(default_factory=set)
    base_generation: int | None = None
    checked_at: float = 0.0


class OverlayStore(ChunkStore):
    """One linked worktree's view of its trunk's shared cache.

    Overlay rows (``checkout`` = the worktree root) hold only files whose
    content differs from base or that base lacks. The worktree sees base
    rows minus its shadowed paths, plus its overlay rows, and writes only
    overlay rows. The connection and table belong to the owning store.
    """

    def __init__(self, owner: ChunkStore, checkout: str) -> None:  # noqa: D107
        self._owner = owner
        self.db_path = owner.db_path
        self.db = owner.db
        self._project_path = owner._project_path
        self.checkout = checkout
        # Overlay rows are written by the current chunker; no repair applies.
        self._line_repair_done = True
        digest = hashlib.sha256(checkout.encode()).hexdigest()[:12]
        self._state_path = owner.db_path / _OVERLAY_DIRNAME / f"{digest}.json"
        self._state = self._load_state()

    @property
    def table(self) -> Table:
        """Get the owning store's chunks table."""
        return self._owner.table

    def ensure_fts_index(self) -> None:
        """Ensure the owning table's FTS index exists."""
        self._owner.ensure_fts_index()

    def prepare_hybrid_indexes(self, vector_index_row_threshold: int = 1000) -> None:
        """Prepare the owning table's hybrid-search indexes."""
        self._owner.prepare_hybrid_indexes(vector_index_row_threshold)

    def clear(self) -> None:
        """Delete this worktree's overlay rows and bookkeeping."""
        self.table.delete(self._own_where())
        self._state = _OverlayState()
        self._state_path.unlink(missing_ok=True)

    def _load_state(self) -> _OverlayState:
        try:
            if not self._state_path.is_file():
                return _OverlayState()
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return _OverlayState(
                shadowed=set(data.get("shadowed", [])),
                zero_chunk_files=set(data.get("zero_chunk_files", [])),
                base_generation=data.get("base_generation"),
                checked_at=float(data.get("checked_at", 0.0)),
            )
        except Exception as e:
            log.warning("overlay_state_read_failed", checkout=self.checkout, error=str(e))
            return _OverlayState()

    def _save_state(self) -> None:
        try:
            _write_json_atomic(
                self._state_path,
                {
                    "checkout": self.checkout,
                    "shadowed": sorted(self._state.shadowed),
                    "zero_chunk_files": sorted(self._state.zero_chunk_files),
                    "base_generation": self._state.base_generation,
                    "checked_at": self._state.checked_at,
                },
            )
        except OSError as e:
            log.warning("overlay_state_write_failed", checkout=self.checkout, error=str(e))

    def _rows_changed(self) -> None:
        """Overlay writes leave the base generation unchanged."""

    def _visible_where(self) -> str:
        base = _BASE_WHERE
        if self._state.shadowed:
            base = f"({_BASE_WHERE} AND file_path NOT IN ({_sql_list(self._state.shadowed)}))"
        return f"{base} OR {self._own_where()}"

    def delete_by_file(self, file_path: str) -> int:
        """Delete the file's overlay rows and hide its base rows."""
        deleted = super().delete_by_file(file_path)
        if file_path not in self._state.shadowed and self._owner.get_file_hash(file_path):
            self._state.shadowed.add(file_path)
            self._save_state()
        return deleted

    def get_file_hash(self, file_path: str) -> str | None:
        """Return the hash of the version of ``file_path`` this worktree sees."""
        own = super().get_file_hash(file_path)
        if own is not None or file_path in self._state.shadowed:
            return own
        return self._owner.get_file_hash(file_path)

    def get_file_hashes(self) -> dict[str, str]:
        """Return hashes of every file this worktree sees: visible base plus overlay."""
        visible = {
            path: file_hash
            for path, file_hash in self._owner.get_file_hashes().items()
            if path not in self._state.shadowed
        }
        visible.update(super().get_file_hashes())
        return visible

    def get_latest_indexed_at(self) -> float:
        """Return the later of the last full comparison and the newest overlay row."""
        return max(self._state.checked_at, super().get_latest_indexed_at())

    def get_zero_chunk_files(self) -> set[str]:
        """Return worktree paths known to produce zero chunks."""
        return set(self._state.zero_chunk_files)

    def add_zero_chunk_files(self, paths: list[str]) -> None:
        """Record worktree paths that produced zero chunks."""
        if paths and not set(paths) <= self._state.zero_chunk_files:
            self._state.zero_chunk_files.update(paths)
            self._save_state()

    def remove_zero_chunk_file(self, file_path: str) -> None:
        """Forget a zero-chunk worktree path, e.g. after it gains content."""
        if file_path in self._state.zero_chunk_files:
            self._state.zero_chunk_files.discard(file_path)
            self._save_state()

    def line_repair_done(self) -> bool:
        """Overlay rows need no line-range repair."""
        return True

    def mark_line_repair_done(self) -> None:
        """Overlay rows need no line-range repair."""

    def needs_full_recheck(self) -> bool:
        """Return whether base changed since the last full comparison.

        A trunk pull rewrites base rows without touching worktree mtimes, so
        only a full re-hash finds files that now match or differ from base.
        """
        return self._state.base_generation != self._owner.base_generation()

    def adopt_base_version(self, file_path: str, file_hash: str) -> bool:
        """Drop the overlay of a file whose content equals base again."""
        if not file_hash or self._owner.get_file_hash(file_path) != file_hash:
            return False
        ChunkStore.delete_by_file(self, file_path)
        self._state.shadowed.discard(file_path)
        self._state.zero_chunk_files.discard(file_path)
        self._save_state()
        return True

    def sync_checkout(self, current: dict[str, str], checked_at: float) -> None:
        """Compare the worktree's files against base.

        Overlay rows of files gone from the worktree or equal to base again
        are deleted. Base paths the worktree lacks or holds with other
        content become shadowed. Records the base generation read before the
        comparison, so a base write during it triggers another one.
        """
        generation = self._owner.base_generation()
        base = self._owner.get_file_hashes()
        own = ChunkStore.get_file_hashes(self)
        drop = [path for path in own if path not in current or base.get(path) == current[path]]
        if drop:
            self.table.delete(f"{self._own_where()} AND file_path IN ({_sql_list(drop)})")
            log.info("overlay_rows_dropped", checkout=self.checkout, files=len(drop))
        self._state.shadowed = {path for path, h in base.items() if current.get(path) != h}
        self._state.zero_chunk_files &= set(current)
        self._state.base_generation = generation
        self._state.checked_at = checked_at
        self._save_state()
