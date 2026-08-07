"""Symbol index storage for lgrep.

Provides atomic JSON-based persistence for symbol indexes.
Each indexed repository gets its own JSON file keyed by a hash of the repo path.

Design decisions:
- JSON (not SQLite) for v2.0 — simple, debuggable, no schema migrations
- Atomic writes via write-to-temp + rename
- File hashes for incremental change detection
- Byte-offset retrieval for symbol source content
- Path traversal safety for all file reads
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import structlog

log = structlog.get_logger()

# Default symbol index storage directory. Importable so sibling modules
# (tools/prune_symbols.py) do not duplicate this default and drift.
DEFAULT_SYMBOLS_DIR = Path.home() / ".cache" / "lgrep" / "symbols"


@dataclass
class CodeIndex:
    """Symbol index for a single repository.

    Attributes:
        repo_path: Absolute path to the repository root
        files: Dict mapping relative file paths to their content hashes
        symbols: Dict mapping symbol IDs to symbol metadata dicts
        occurrences: Dict mapping identifier names to lists of occurrence dicts
        version: Index format version (for future compatibility)
    """

    repo_path: str
    files: dict[str, str]  # relative_path → hash
    symbols: dict[str, dict]  # symbol_id → symbol metadata
    occurrences: dict[str, list[dict]] = field(default_factory=dict)  # name → occurrences
    version: str = "2.0"


def normalize_repo_key(repo_path: str) -> str:
    """Normalize a repository identifier used for index storage lookup.

    Local repositories are normalized to absolute resolved paths.
    GitHub repositories use symbolic keys in the form "github:owner/name@ref".
    """
    if repo_path.startswith("github:"):
        return repo_path
    return str(Path(repo_path).resolve())


def _repo_key(repo_path: str) -> str:
    """Generate a stable filename key for a repo path."""
    return hashlib.sha256(repo_path.encode()).hexdigest()[:16]


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse an index version string into a comparable tuple of integers."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def _unique_temp_path(target: Path) -> Path:
    """Return a writer-unique staging path beside *target*.

    The temp name must NOT be derivable from the target alone. A deterministic
    name (``target.with_suffix(".tmp")``) is shared by every concurrent writer
    of the same repo key, which produces two distinct failures:

    - the writers interleave bytes into one file, and a torn blob is then
      atomically renamed into place; and
    - the first ``os.replace`` consumes the shared temp file, so the remaining
      writers fail with ``FileNotFoundError`` and their saves are lost.

    Including the pid and a random token gives each writer a private staging
    file, so the committed artifact is always exactly one writer's complete
    output. No lock is required: ``save`` is a whole-file overwrite that
    derives nothing from the file it replaces.
    """
    return target.with_name(f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def _sidecar_for_index(index_file: Path) -> Path:
    """Return the sidecar path paired with an ``index_{key}.json`` file."""
    return index_file.with_name(f"{index_file.stem}.meta.json")


def _read_sidecar_repo_path(index_file: Path) -> str | None:
    """Read ``repo_path`` from an index's sidecar, with key verification.

    Returns ``None`` — the caller must fall back to parsing the index — when
    the sidecar is missing, unreadable, corrupt, or carries a ``repo_path``
    that does not hash back to this index's key. The sidecar is untrusted
    disk input and advisory only: verifying ``_repo_key(repo_path)`` against
    the filename key (P33/P38) means a copied or hand-edited sidecar degrades
    to the authoritative parse path instead of poisoning results.
    """
    sidecar = _sidecar_for_index(index_file)
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    repo_path = data.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path:
        return None
    key = index_file.stem[len("index_"):]
    try:
        if _repo_key(normalize_repo_key(repo_path)) != key:
            return None
    except (OSError, ValueError):
        return None
    return repo_path


def _write_sidecar(index_file: Path, meta: dict) -> None:
    """Write an index's sidecar via writer-unique temp + os.replace.

    Best-effort: the sidecar is advisory, so a failed write (read-only or
    full filesystem) must degrade to "slow but correct" — never raise. The
    caller's next read takes the parse fallback and retries the backfill.
    """
    sidecar = _sidecar_for_index(index_file)
    tmp = _unique_temp_path(sidecar)
    try:
        tmp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, sidecar)
    except OSError as e:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        log.warning("index_sidecar_save_failed", path=str(sidecar), error=str(e))


def _meta_from_index_body(repo_path: str, data: dict) -> dict:
    """Build sidecar content from a parsed index body (backfill path)."""
    occurrences = data.get("occurrences") or {}
    return {
        "repo_path": repo_path,
        "version": data.get("version", "2.0"),
        "meta_version": 1,
        "files": len(data.get("files") or {}),
        "symbols": len(data.get("symbols") or {}),
        "occurrences": sum(len(v) for v in occurrences.values()),
        "updated_at": time.time(),
    }


_fcntl_unavailable_warned = False


class IndexStore:
    """Persistent symbol index storage.

    Stores one JSON file per indexed repository in the storage directory.
    All writes are atomic (write-to-temp + rename).

    Usage:
        store = IndexStore(storage_dir=Path("~/.cache/lgrep/symbols"))
        store.save(index)
        index = store.load("/path/to/repo")
        repos = store.list_repos()
        store.delete_index("/path/to/repo")
    """

    _cache: ClassVar[dict[Path, tuple[int, int, CodeIndex]]] = {}

    def __init__(self, storage_dir: Path | str | None = None) -> None:
        """Initialize the index store.

        Args:
            storage_dir: Directory to store index files. Defaults to
                         ~/.cache/lgrep/symbols/
        """
        if storage_dir is None:
            storage_dir = DEFAULT_SYMBOLS_DIR
        self._dir = Path(storage_dir)

    def _index_path(self, repo_path: str) -> Path:
        """Get the index file path for a repo."""
        key = _repo_key(repo_path)
        return self._dir / f"index_{key}.json"

    def _meta_path(self, repo_path: str) -> Path:
        """Get the metadata sidecar path for a repo's index.

        The sidecar carries the one load-bearing field (``repo_path``) plus
        informational counts so ``list_repos()`` never needs to parse the
        index body. It is an advisory cache, never authoritative — readers
        must fall back to parsing the index when it is missing, corrupt, or
        fails key verification.
        """
        key = _repo_key(repo_path)
        return self._dir / f"index_{key}.meta.json"

    @contextlib.contextmanager
    def repo_lock(self, repo_path: str):
        """Serialize read-modify-write sequences for one repo key.

        ``save()`` alone cannot prevent a lost update: a caller that does
        ``load()`` -> mutate -> ``save()`` (``index_folder(incremental=True)``
        does exactly this) computes its write from a snapshot that a
        concurrent run may have already replaced. Holding this lock across
        the whole window serializes those sequences so every writer merges
        on top of the previous writer's committed output.

        Mechanism (mirrors ``_chunk_store.write_project_meta``):

        - ``fcntl.flock(LOCK_EX)`` on a dedicated sibling file
          ``.index_{key}.lock`` — never on the index or sidecar, because
          ``os.replace`` swaps the inode and would invalidate lock identity.
        - A FRESH fd per acquisition. Per flock(2), fds from separate
          ``open()`` calls are treated independently and block each other —
          including two threads in one process (verified empirically and in
          the man page) — while fds duplicated via fork/dup share one lock,
          so an fd must never be cached across acquisitions. This one
          mechanism therefore covers both threads and processes.
        - The lock file is NEVER deleted: unlinking an inode another process
          is waiting on silently breaks mutual exclusion.
        - Non-POSIX platforms (no ``fcntl``): warn once and proceed
          unguarded, matching the degraded-support stance in
          ``_chunk_store``.
        - Per-key granularity: unrelated repos never serialize.

        Deliberately NOT held inside ``save()`` itself: the hazard is the
        caller's stale read, which a lock inside ``save()`` cannot reach,
        and a lock there would not be re-entrant with the caller's hold.
        """
        global _fcntl_unavailable_warned
        normalized_repo = normalize_repo_key(repo_path)
        key = _repo_key(normalized_repo)
        lock_path = self._dir / f".index_{key}.lock"

        fcntl_mod = None
        try:
            import fcntl as _fcntl

            fcntl_mod = _fcntl
        except ImportError:
            if not _fcntl_unavailable_warned:
                _fcntl_unavailable_warned = True
                log.warning(
                    "fcntl_unavailable_repo_lock_unguarded",
                    note="non-POSIX platform; incremental symbol indexing unguarded across processes",
                )

        if fcntl_mod is None:
            yield
            return

        self._dir.mkdir(parents=True, exist_ok=True)
        lock_fd = None
        try:
            lock_fd = open(lock_path, "a+")  # noqa: SIM115 — closed in finally
            fcntl_mod.flock(lock_fd.fileno(), fcntl_mod.LOCK_EX)
        except OSError:
            log.warning("flock_setup_failed", repo=normalized_repo)
            if lock_fd is not None:
                lock_fd.close()
                lock_fd = None

        try:
            yield
        finally:
            if lock_fd is not None:
                with contextlib.suppress(OSError):
                    fcntl_mod.flock(lock_fd.fileno(), fcntl_mod.LOCK_UN)
                lock_fd.close()

    def save(self, index: CodeIndex) -> None:
        """Save a CodeIndex to disk atomically.

        Uses write-to-temp + rename for atomicity.

        Args:
            index: The CodeIndex to persist
        """
        normalized_repo = normalize_repo_key(index.repo_path)
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._index_path(normalized_repo)
        tmp = _unique_temp_path(target)

        try:
            data = {
                "repo_path": normalized_repo,
                "files": index.files,
                "symbols": index.symbols,
                "occurrences": index.occurrences,
                "version": index.version,
            }
            # Compact serialization. Measured on the real symbol store,
            # pretty-print padding was 19.1% of stored bytes (~480MB of 2.5GB).
            # json.loads is whitespace-agnostic, so existing pretty-printed
            # indexes remain readable with no migration.
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            # os.replace (not Path.rename): os.rename passes flags=0 on Windows
            # and raises FileExistsError when the target already exists, which
            # breaks re-indexing there. os.replace overwrites atomically on
            # both POSIX and Windows.
            os.replace(tmp, target)
            # Sidecar AFTER the index: the only crash window then leaves
            # index-without-sidecar, which readers handle via the parse
            # fallback. Sidecar-first could leave sidecar-without-index, a
            # state list_repos() must never observe.
            meta = {
                "repo_path": normalized_repo,
                "version": index.version,
                "meta_version": 1,
                "files": len(index.files),
                "symbols": len(index.symbols),
                "occurrences": sum(len(v) for v in index.occurrences.values()),
                "updated_at": time.time(),
            }
            _write_sidecar(target, meta)
            stat = target.stat()
            self._cache[target] = (stat.st_mtime_ns, stat.st_size, index)
            log.debug(
                "index_saved",
                repo=normalized_repo,
                symbols=len(index.symbols),
                occurrences=sum(len(v) for v in index.occurrences.values()),
            )
        except OSError as e:
            log.error("index_save_failed", repo=normalized_repo, error=str(e))
            # Clean up temp file if it exists
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    def load(self, repo_path: str) -> CodeIndex | None:
        """Load a CodeIndex from disk.

        Args:
            repo_path: Absolute path to the repository root

        Returns:
            CodeIndex if found, None if not indexed yet
        """
        normalized_repo = normalize_repo_key(repo_path)
        index_file = self._index_path(normalized_repo)
        if not index_file.exists():
            return None

        try:
            stat = index_file.stat()
            cached = self._cache.get(index_file)
            if cached is not None:
                cached_mtime_ns, cached_size, cached_index = cached
                if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                    return cached_index

            data = json.loads(index_file.read_text(encoding="utf-8"))
            index = CodeIndex(
                repo_path=data["repo_path"],
                files=data.get("files", {}),
                symbols=data.get("symbols", {}),
                occurrences=data.get("occurrences", {}),
                version=data.get("version", "2.0"),
            )
            self._cache[index_file] = (stat.st_mtime_ns, stat.st_size, index)
            return index
        except (json.JSONDecodeError, KeyError, OSError) as e:
            log.warning("index_load_failed", repo=normalized_repo, error=str(e))
            return None

    def list_repos(self) -> list[str]:
        """Return all indexed repository paths.

        Returns:
            List of absolute repo paths that have been indexed
        """
        if not self._dir.exists():
            return []

        repos = []
        for index_file in self._dir.glob("index_*.json"):
            # Iteration is driven by INDEX files only. Sidecars never enter
            # the candidate set, so an orphaned sidecar (index deleted out
            # of band) can never surface as a live repo — AC9 is a property
            # of this loop, not a runtime check.
            if ".meta." in index_file.name:
                continue
            repo_path = _read_sidecar_repo_path(index_file)
            if repo_path is None:
                # Missing / corrupt / foreign-key sidecar: authoritative path.
                try:
                    data = json.loads(index_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(data, dict):
                    continue
                candidate = data.get("repo_path")
                if not isinstance(candidate, str) or not candidate:
                    continue
                repo_path = candidate
                # Backfill so the NEXT list_repos() is cheap. Best-effort;
                # failure here must never break listing.
                _write_sidecar(index_file, _meta_from_index_body(repo_path, data))
            repos.append(repo_path)

        return repos

    def delete_index(self, repo_path: str) -> None:
        """Delete the index for a repository.

        Args:
            repo_path: Absolute path to the repository root
        """
        normalized_repo = normalize_repo_key(repo_path)
        index_file = self._index_path(normalized_repo)
        try:
            index_file.unlink(missing_ok=True)
            # Remove the sidecar with its index (AC4). The prune path must
            # handle its own orphan case — it unlinks index files directly
            # and never routes through this method.
            self._meta_path(normalized_repo).unlink(missing_ok=True)
            self._cache.pop(index_file, None)
            log.info("index_deleted", repo=normalized_repo)
        except OSError as e:
            log.warning("index_delete_failed", repo=normalized_repo, error=str(e))

    def detect_changes(
        self,
        repo_path: str,
        current_files: dict[str, str],
    ) -> dict[str, list[str]]:
        """Detect changed, new, and deleted files since last index.

        Args:
            repo_path: Absolute path to the repository root
            current_files: Dict mapping relative file paths to their current hashes

        Returns:
            Dict with keys "new", "changed", "deleted" — each a list of file paths
        """
        index = self.load(repo_path)
        if index is None:
            # No existing index — everything is new
            return {"new": list(current_files.keys()), "changed": [], "deleted": []}

        indexed_files = index.files
        new_files = []
        changed_files = []
        deleted_files = []

        for path, current_hash in current_files.items():
            if path not in indexed_files:
                new_files.append(path)
            elif indexed_files[path] != current_hash:
                changed_files.append(path)

        for path in indexed_files:
            if path not in current_files:
                deleted_files.append(path)

        return {"new": new_files, "changed": changed_files, "deleted": deleted_files}

    def get_symbol_content(
        self,
        file_path: Path | str,
        start_byte: int,
        end_byte: int,
    ) -> bytes | None:
        """Retrieve the source bytes for a symbol by byte offset.

        Includes path traversal safety — rejects paths that resolve outside
        the file's own directory.

        Args:
            file_path: Path to the source file
            start_byte: Start byte offset of the symbol
            end_byte: End byte offset of the symbol

        Returns:
            Source bytes for the symbol, or None if the file cannot be read
            or the path is unsafe.
        """
        file_path = Path(file_path)

        # Path traversal safety: resolve and check for .. components
        try:
            resolved = file_path.resolve()
            # Reject if the resolved path differs significantly from the input
            # (i.e., .. components were present that escaped the directory)
            if ".." in str(file_path):
                log.warning("index_store_path_traversal_rejected", path=str(file_path))
                return None
        except OSError:
            return None

        if not resolved.exists():
            return None

        try:
            content = resolved.read_bytes()
            return content[start_byte:end_byte]
        except OSError as e:
            log.warning("symbol_content_read_failed", file=str(file_path), error=str(e))
            return None
