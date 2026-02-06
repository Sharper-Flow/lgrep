"""Indexing logic for lgrep.

Wires together file discovery, chunking, embedding, and storage.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from lgrep.chunking import CodeChunker
from lgrep.discovery import FileDiscovery
from lgrep.storage import CodeChunk

if TYPE_CHECKING:
    from lgrep.embeddings import VoyageEmbedder
    from lgrep.storage import ChunkStore

log = structlog.get_logger()


@dataclass
class IndexStatus:
    """Status of an indexing operation."""

    file_count: int = 0
    chunk_count: int = 0
    duration_ms: float = 0.0
    total_tokens: int = 0


class Indexer:
    """Coordinates the full indexing pipeline."""

    def __init__(
        self,
        project_path: str | Path,
        storage: ChunkStore,
        embedder: VoyageEmbedder,
        chunk_size: int = 500,
    ) -> None:
        """Initialize the indexer.

        Args:
            project_path: Absolute path to the project root
            storage: ChunkStore instance
            embedder: VoyageEmbedder instance
            chunk_size: Token size for chunks
        """
        self.project_path = Path(project_path).resolve()
        self.storage = storage
        self.embedder = embedder
        self.chunker = CodeChunker(chunk_size=chunk_size)
        self.discovery = FileDiscovery(self.project_path)

        log.info("indexer_initialized", project=str(self.project_path))

    def index_all(self) -> IndexStatus:
        """Perform a full index of the project.

        Returns:
            IndexStatus with results
        """
        start_time = time.perf_counter()
        status = IndexStatus()

        log.info("full_index_started", project=str(self.project_path))

        all_files = list(self.discovery.find_files())
        status.file_count = len(all_files)

        # Remove stale chunks for files that no longer exist on disk
        try:
            indexed_files = self.storage.get_indexed_files()
            current_rel_paths = {str(Path(f).relative_to(self.project_path)) for f in all_files}
            stale_files = indexed_files - current_rel_paths
            for stale_path in stale_files:
                self.storage.delete_by_file(stale_path)
                log.info("stale_file_removed", file=stale_path)
        except Exception as e:
            log.warning("stale_cleanup_failed", error=str(e))

        for file_path in all_files:
            file_status = self.index_file(file_path)
            status.chunk_count += file_status.chunk_count
            status.total_tokens += file_status.total_tokens

        status.duration_ms = (time.perf_counter() - start_time) * 1000

        log.info(
            "full_index_complete",
            files=status.file_count,
            chunks=status.chunk_count,
            duration_ms=status.duration_ms,
        )

        return status

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

    def index_file(self, file_path: str | Path) -> IndexStatus:
        """Index or re-index a single file.

        Args:
            file_path: Absolute or relative path to the file

        Returns:
            IndexStatus for this file
        """
        start_time = time.perf_counter()
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
        texts = [c.text for c in chunk_result.chunks]
        embed_result = self.embedder.embed_documents(texts)

        # 3. Storage
        self.storage.delete_by_file(rel_path)
        code_chunks = self._build_code_chunks(
            chunk_result.chunks, embed_result.embeddings, rel_path, file_hash
        )
        self.storage.add_chunks(code_chunks)

        status = IndexStatus(
            file_count=1,
            chunk_count=len(code_chunks),
            duration_ms=(time.perf_counter() - start_time) * 1000,
            total_tokens=embed_result.token_usage,
        )

        log.debug(
            "file_indexed",
            file=rel_path,
            chunks=status.chunk_count,
            tokens=status.total_tokens,
        )

        return status
