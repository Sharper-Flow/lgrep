"""Indexing logic for lgrep.

Wires together file discovery, chunking, embedding, and storage.
"""

from __future__ import annotations

import hashlib
import os
import threading  # noqa: TC003  # used at runtime by cancel_event.is_set()
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.chunking import CodeChunker
from lgrep.discovery import FileDiscovery
from lgrep.exceptions import OperationCancelled
from lgrep.storage import CodeChunk

if TYPE_CHECKING:
    from collections.abc import Callable

    from lgrep.embeddings import VoyageEmbedder
    from lgrep.storage import ChunkStore

log = structlog.get_logger()

# Re-exported for backward compatibility: callers that do
# `from lgrep.indexing import OperationCancelled` (lifecycle.py, v1 tests)
# keep working after the class moved to lgrep.exceptions to break the
# indexing <-> embeddings import cycle.
__all__ = ["IndexStatus", "IndexWindowResult", "Indexer", "OperationCancelled"]


@dataclass
class IndexStatus:
    """Status of an indexing operation."""

    file_count: int = 0
    chunk_count: int = 0
    duration_ms: float = 0.0
    total_tokens: int = 0


@dataclass
class IndexWindowResult:
    """Result of one bounded indexing window.

    ``complete`` means no pending files remain. ``remaining_files`` is the
    deterministic ordered list of relative paths that still need indexing so
    the next window can resume without re-walking already-indexed files.
    """

    status: IndexStatus
    complete: bool
    remaining_files: list[str]
    indexed_files: list[str]
    files_indexed: int = 0


class Indexer:
    """Coordinates the full indexing pipeline."""

    def __init__(
        self,
        project_path: str | Path,
        storage: ChunkStore,
        embedder: VoyageEmbedder,
        chunk_size: int = 500,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the indexer.

        Args:
            project_path: Absolute path to the project root
            storage: ChunkStore instance
            embedder: VoyageEmbedder instance
            chunk_size: Token size for chunks
            perf_counter: Optional monotonic clock callable. Defaults to
                ``time.perf_counter`` so production keeps wall-clock behavior;
                tests may inject a deterministic advancing clock.
        """
        self.project_path = Path(project_path).resolve()
        self.storage = storage
        self.embedder = embedder
        self.chunker = CodeChunker(chunk_size=chunk_size)
        self.discovery = FileDiscovery(self.project_path)
        self._dedup_enabled = bool(os.environ.get("LGREP_WORKTREE_DEDUP"))
        self._perf_counter = perf_counter or time.perf_counter

        log.info("indexer_initialized", project=str(self.project_path))

    def index_all(self, cancel_event: threading.Event | None = None) -> IndexStatus:
        """Perform a full index of the project across bounded windows.

        Splits the work into multiple :meth:`index_window` calls so that each
        window stays within ``LGREP_INDEX_MAX_WALL_S``. The loop continues until
        the pending set is empty or the operation is cancelled.

        Args:
            cancel_event: Optional cooperative-cancellation primitive. If
                set, the current window exits at the next file boundary with
                ``OperationCancelled``.

        Returns:
            IndexStatus with cumulative results.

        Raises:
            OperationCancelled: if ``cancel_event`` is set before or during
                a window.
        """
        start_time = self._perf_counter()
        status = IndexStatus()
        pending: list[str] | None = None

        log.info("full_index_started", project=str(self.project_path))

        while True:
            window = self.index_window(cancel_event=cancel_event, pending_files=pending)
            status.file_count += window.status.file_count
            status.chunk_count += window.status.chunk_count
            status.total_tokens += window.status.total_tokens
            if window.complete:
                break
            pending = window.remaining_files

        status.duration_ms = (self._perf_counter() - start_time) * 1000

        log.info(
            "full_index_complete",
            files=status.file_count,
            chunks=status.chunk_count,
            duration_ms=status.duration_ms,
        )

        return status

    def index_window(
        self,
        cancel_event: threading.Event | None = None,
        pending_files: list[str] | None = None,
    ) -> IndexWindowResult:
        """Run one bounded index window.

        If ``pending_files`` is not supplied, the method enumerates all
        discoverable files, removes stale indexed files, and computes a
        deterministic pending sequence from a batched hash projection.  When
        ``pending_files`` is supplied, the window resumes from that list
        without re-walking the repository.

        Each window processes at least one pending file before checking the
        wall-clock budget.  When the budget is exceeded the window returns the
        remaining pending paths, and calls ``prepare_hybrid_indexes`` at the
        boundary so the partial corpus is usable for hybrid search.

        Args:
            cancel_event: Optional cooperative-cancellation primitive. If set,
                raises ``OperationCancelled`` at the next file boundary.
            pending_files: Optional ordered list of relative paths to resume
                indexing from.  When omitted, the pending set is recomputed.

        Returns:
            IndexWindowResult describing the window's progress and any
            remaining work.

        Raises:
            OperationCancelled: if ``cancel_event`` is set.
        """
        start_time = self._perf_counter()
        wall_budget_s = float(os.environ.get("LGREP_INDEX_MAX_WALL_S", "60.0"))
        status = IndexStatus()

        if pending_files is None:
            log.info("index_window_computing_pending", project=str(self.project_path))
            pending_files = self.compute_pending_files()
        else:
            pending_files = sorted(pending_files)

        indexed_this_window: list[str] = []
        zero_chunk_this_window: list[str] = []
        remaining_files = list(pending_files)
        processed = False

        for rel_path in pending_files:
            if cancel_event is not None and cancel_event.is_set():
                log.info(
                    "index_window_cancelled",
                    project=str(self.project_path),
                    files_indexed=len(indexed_this_window),
                    remaining_files=len(remaining_files),
                )
                self.storage.prepare_hybrid_indexes()
                if zero_chunk_this_window:
                    self.storage.add_zero_chunk_files(zero_chunk_this_window)
                raise OperationCancelled("index_window cancelled by cancel_event")

            # After the first file, respect the wall budget.  The first file
            # is always processed so a window never yields with zero progress.
            if processed and (self._perf_counter() - start_time) > wall_budget_s:
                log.warning(
                    "index_window_wall_clock_exceeded",
                    project=str(self.project_path),
                    budget_s=wall_budget_s,
                    files_indexed=len(indexed_this_window),
                    remaining_files=len(remaining_files),
                )
                break

            file_path = self.project_path / rel_path
            try:
                file_status = self.index_file(file_path, cancel_event=cancel_event)
            except OperationCancelled:
                self.storage.prepare_hybrid_indexes()
                if zero_chunk_this_window:
                    self.storage.add_zero_chunk_files(zero_chunk_this_window)
                raise
            status.file_count += file_status.file_count
            status.chunk_count += file_status.chunk_count
            status.total_tokens += file_status.total_tokens
            indexed_this_window.append(rel_path)
            remaining_files.remove(rel_path)
            if file_status.chunk_count == 0:
                zero_chunk_this_window.append(rel_path)
            else:
                self.storage.remove_zero_chunk_file(rel_path)
            processed = True

            if (self._perf_counter() - start_time) > wall_budget_s:
                log.warning(
                    "index_window_wall_clock_exceeded",
                    project=str(self.project_path),
                    budget_s=wall_budget_s,
                    files_indexed=len(indexed_this_window),
                    remaining_files=len(remaining_files),
                )
                break

        self.storage.prepare_hybrid_indexes()
        if zero_chunk_this_window:
            self.storage.add_zero_chunk_files(zero_chunk_this_window)

        status.duration_ms = (self._perf_counter() - start_time) * 1000

        log.info(
            "index_window_complete",
            project=str(self.project_path),
            complete=len(remaining_files) == 0,
            files=status.file_count,
            chunks=status.chunk_count,
            duration_ms=status.duration_ms,
            remaining_files=len(remaining_files),
        )

        return IndexWindowResult(
            status=status,
            complete=len(remaining_files) == 0,
            remaining_files=remaining_files,
            indexed_files=indexed_this_window,
            files_indexed=len(indexed_this_window),
        )

    def compute_pending_files(self) -> list[str]:
        """Return the deterministic ordered list of files needing indexing.

        Compares discovered files against stored hashes using one batched
        projection. Files whose hash matches the stored hash are omitted.
        Stale indexed files (present in storage but absent from disk) are
        removed when worktree dedup is disabled.
        """
        all_files = list(self.discovery.find_files())
        current_rel_paths = {str(Path(f).relative_to(self.project_path)) for f in all_files}

        # Remove stale chunks for files that no longer exist on disk.
        if not self._dedup_enabled:
            try:
                indexed_files = self.storage.get_indexed_files()
                stale_files = indexed_files - current_rel_paths
                for stale_path in stale_files:
                    self.storage.delete_by_file(stale_path)
                    log.info("stale_file_removed", file=stale_path)
            except Exception as e:
                log.warning("stale_cleanup_failed", error=str(e))

        try:
            stored_hashes = self.storage.get_file_hashes()
        except Exception as e:
            log.warning("batched_hash_lookup_failed", error=str(e))
            stored_hashes = {}

        pending: list[str] = []
        try:
            zero_chunk_files = set(self.storage.get_zero_chunk_files())
        except Exception:
            zero_chunk_files = set()
        for file_path in all_files:
            rel_path = str(file_path.relative_to(self.project_path))
            if rel_path in zero_chunk_files:
                continue
            file_hash = self._compute_file_hash(file_path, rel_path)
            if file_hash and stored_hashes.get(rel_path) == file_hash:
                continue
            pending.append(rel_path)
        pending.sort()
        return pending

    def _compute_file_hash(self, file_path: Path, rel_path: str) -> str:
        """Compute SHA-256 hash of a file for cache invalidation."""
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            log.debug("file_hash_failed", file=rel_path, error=str(e))
            return ""

    def _build_code_chunks(
        self,
        chunk_infos: list,
        embeddings: list[list[float]],
        rel_path: str,
        file_hash: str,
    ) -> list[CodeChunk]:
        """Create CodeChunk objects from chunk info and embedding vectors."""
        now = time.time()
        return [
            CodeChunk(
                id=str(uuid.uuid4()),
                file_path=rel_path,
                chunk_index=i,
                start_line=chunk_info.start_line,
                end_line=chunk_info.end_line,
                content=chunk_info.text,
                vector=vector,
                file_hash=file_hash,
                indexed_at=now,
            )
            for i, (chunk_info, vector) in enumerate(zip(chunk_infos, embeddings, strict=False))
        ]

    def index_file(
        self, file_path: str | Path, cancel_event: threading.Event | None = None
    ) -> IndexStatus:
        """Index or re-index a single file.

        Args:
            file_path: Absolute or relative path to the file
            cancel_event: Optional cooperative-cancellation primitive. If
                set, raises ``OperationCancelled`` before the embedding step
                or before the storage step.

        Returns:
            IndexStatus for this file

        Raises:
            OperationCancelled: if ``cancel_event`` is set before embedding
                or before storage.
        """
        start_time = self._perf_counter()
        file_path = Path(file_path)
        if not file_path.is_absolute():
            file_path = self.project_path / file_path

        rel_path = str(file_path.relative_to(self.project_path))

        # Check if file has changed before doing expensive embedding
        file_hash = self._compute_file_hash(file_path, rel_path)
        if file_hash:
            stored_hash = self.storage.get_file_hash(rel_path)
            if stored_hash == file_hash:
                log.debug("file_unchanged_skipping", file=rel_path)
                return IndexStatus(file_count=1)

        # 1. Chunking
        chunk_result = self.chunker.chunk_file(file_path)
        if chunk_result.error:
            log.warning("indexing_file_failed", file=rel_path, error=chunk_result.error)
            return IndexStatus(file_count=0)

        if not chunk_result.chunks:
            # File exists but produced no chunks (e.g. empty or only comments)
            self.storage.delete_by_file(rel_path)
            return IndexStatus(file_count=1)

        # 2. Embedding
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("index_file cancelled before embed")
        texts = [c.text for c in chunk_result.chunks]
        embed_result = self.embedder.embed_documents(texts, cancel_event=cancel_event)

        # 3. Storage
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("index_file cancelled before storage")
        self.storage.delete_by_file(rel_path)
        code_chunks = self._build_code_chunks(
            chunk_result.chunks, embed_result.embeddings, rel_path, file_hash
        )
        self.storage.add_chunks(code_chunks)

        status = IndexStatus(
            file_count=1,
            chunk_count=len(code_chunks),
            duration_ms=(self._perf_counter() - start_time) * 1000,
            total_tokens=embed_result.token_usage,
        )

        log.debug(
            "file_indexed",
            file=rel_path,
            chunks=status.chunk_count,
            tokens=status.total_tokens,
        )

        return status
