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
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import structlog

log = structlog.get_logger()


@dataclass
class CodeIndex:
    """Symbol index for a single repository.

    Attributes:
        repo_path: Absolute path to the repository root
        files: Dict mapping relative file paths to their content hashes
        symbols: Dict mapping symbol IDs to symbol metadata dicts
        version: Index format version (for future compatibility)
    """

    repo_path: str
    files: dict[str, str]  # relative_path → hash
    symbols: dict[str, dict]  # symbol_id → symbol metadata
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
            storage_dir = Path.home() / ".cache" / "lgrep" / "symbols"
        self._dir = Path(storage_dir)

    def _index_path(self, repo_path: str) -> Path:
        """Get the index file path for a repo."""
        key = _repo_key(repo_path)
        return self._dir / f"index_{key}.json"

    def save(self, index: CodeIndex) -> None:
        """Save a CodeIndex to disk atomically.

        Uses write-to-temp + rename for atomicity.

        Args:
            index: The CodeIndex to persist
        """
        normalized_repo = normalize_repo_key(index.repo_path)
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._index_path(normalized_repo)
        tmp = target.with_suffix(".tmp")

        try:
            data = {
                "repo_path": normalized_repo,
                "files": index.files,
                "symbols": index.symbols,
                "version": index.version,
            }
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.rename(target)
            stat = target.stat()
            self._cache[target] = (stat.st_mtime_ns, stat.st_size, index)
            log.debug("index_saved", repo=normalized_repo, symbols=len(index.symbols))
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
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                repos.append(data["repo_path"])
            except (json.JSONDecodeError, KeyError, OSError):
                pass

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

    def incremental_save(
        self,
        repo_path: str,
        updated_files: dict[str, str],
        updated_symbols: dict[str, dict],
        deleted_files: list[str],
    ) -> None:
        """Update an existing index incrementally.

        Merges updated files/symbols into the existing index and removes
        symbols for deleted files.

        Args:
            repo_path: Absolute path to the repository root
            updated_files: Dict of file paths → hashes to add/update
            updated_symbols: Dict of symbol IDs → metadata to add/update
            deleted_files: List of file paths whose symbols should be removed
        """
        index = self.load(repo_path) or CodeIndex(repo_path=repo_path, files={}, symbols={})

        # Update file hashes
        index.files.update(updated_files)
        for path in deleted_files:
            index.files.pop(path, None)

        # Update symbols
        index.symbols.update(updated_symbols)

        # Remove symbols for deleted files
        if deleted_files:
            deleted_set = set(deleted_files)
            to_remove = [
                sym_id
                for sym_id, sym_data in index.symbols.items()
                if sym_data.get("file_path") in deleted_set
            ]
            for sym_id in to_remove:
                del index.symbols[sym_id]

        self.save(index)

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
