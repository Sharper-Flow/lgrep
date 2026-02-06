"""Code chunking for lgrep using Chonkie.

Uses AST-aware chunking via tree-sitter for better semantic boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

# Language detection by file extension
LANGUAGE_MAP = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".sql": "sql",
}

# Default chunk size (tokens)
DEFAULT_CHUNK_SIZE = 500
MIN_CHUNK_TOKENS = 10  # Skip tiny chunks


@dataclass
class CodeChunkResult:
    """Result of chunking a file."""

    file_path: str
    chunks: list[ChunkInfo] = field(default_factory=list)
    language: str | None = None
    error: str | None = None


@dataclass
class ChunkInfo:
    """Information about a single chunk."""

    text: str
    token_count: int
    chunk_index: int
    start_line: int
    end_line: int


def detect_language(file_path: str | Path) -> str | None:
    """Detect programming language from file extension.

    Args:
        file_path: Path to the file

    Returns:
        Language name for Chonkie, or None if unsupported
    """
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


class CodeChunker:
    """Chunker for source code files using AST-aware splitting.

    Uses Chonkie's CodeChunker with tree-sitter for semantic boundaries.
    Falls back to simple text chunking for unsupported languages.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        """Initialize the chunker.

        Args:
            chunk_size: Target chunk size in tokens (default 500)
        """
        self.chunk_size = chunk_size
        self._chunkers: dict[str, object] = {}
        log.info("code_chunker_initialized", chunk_size=chunk_size)

    def _get_chunker(self, language: str):
        """Get or create a Chonkie chunker for a language.

        Note: Uses Chonkie's experimental AST-aware chunker. This API may change.
        """
        if language not in self._chunkers:
            try:
                from chonkie.experimental import CodeChunker as ChonkieCodeChunker

                self._chunkers[language] = ChonkieCodeChunker(
                    language=language,
                    chunk_size=self.chunk_size,
                )
                log.debug("chunker_created", language=language)
            except Exception as e:
                log.warning("chunker_creation_failed", language=language, error=str(e))
                return None
        return self._chunkers.get(language)

    def _read_file_content(self, file_path: Path) -> str | None:
        """Read file content, returning None on failure."""
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("file_read_failed", file=str(file_path), error=str(e))
            return None

    def _try_ast_chunk(self, content: str, language: str, str_path: str) -> list[ChunkInfo] | None:
        """Attempt AST-based chunking, returning None on failure."""
        chunker = self._get_chunker(language)
        if not chunker:
            return None
        try:
            raw_chunks = chunker.chunk(content)
            chunks = self._process_chunks(raw_chunks, content)
            log.debug(
                "file_chunked",
                file=str_path,
                language=language,
                chunks=len(chunks),
            )
            return chunks
        except Exception as e:
            log.warning("ast_chunking_failed", file=str_path, error=str(e))
            return None

    def chunk_file(self, file_path: str | Path, content: str | None = None) -> CodeChunkResult:
        """Chunk a source code file.

        Args:
            file_path: Path to the file (used for language detection)
            content: File content (if None, reads from file_path)

        Returns:
            CodeChunkResult with chunks and metadata
        """
        file_path = Path(file_path)
        str_path = str(file_path)

        # Read content if not provided
        if content is None:
            content = self._read_file_content(file_path)
            if content is None:
                return CodeChunkResult(
                    file_path=str_path,
                    error=f"Failed to read file: {file_path}",
                )

        if not content.strip():
            return CodeChunkResult(file_path=str_path, language=None)

        language = detect_language(file_path)

        # Try AST-based chunking, then fallback to text
        chunks = None
        if language:
            chunks = self._try_ast_chunk(content, language, str_path)

        if chunks is None:
            chunks = self._fallback_chunk(content)
            log.debug("file_chunked_fallback", file=str_path, chunks=len(chunks))

        return CodeChunkResult(
            file_path=str_path,
            chunks=chunks,
            language=language,
        )

    def _process_chunks(self, raw_chunks: list, content: str) -> list[ChunkInfo]:
        """Process Chonkie chunks into ChunkInfo objects.

        Filters out tiny chunks and calculates line numbers.
        """
        chunks = []
        lines = content.split("\n")
        line_starts = [0]  # Cumulative char positions for each line
        for line in lines:
            line_starts.append(line_starts[-1] + len(line) + 1)

        for i, raw in enumerate(raw_chunks):
            text = raw.text.strip()
            token_count = getattr(raw, "token_count", len(text.split()))

            # Skip tiny/empty chunks
            if token_count < MIN_CHUNK_TOKENS or not text:
                continue

            # Calculate line numbers
            # Find the chunk in content and get line numbers
            start_line = 1
            end_line = 1
            try:
                pos = content.find(text[: min(50, len(text))])
                if pos >= 0:
                    # Find which line this position is on
                    for line_num, start_pos in enumerate(line_starts):
                        if start_pos > pos:
                            start_line = line_num
                            break
                    end_pos = pos + len(text)
                    for line_num, start_pos in enumerate(line_starts):
                        if start_pos > end_pos:
                            end_line = line_num
                            break
            except Exception as e:
                log.debug("line_number_calc_failed", chunk_index=i, error=str(e))

            chunks.append(
                ChunkInfo(
                    text=text,
                    token_count=token_count,
                    chunk_index=len(chunks),
                    start_line=start_line,
                    end_line=end_line,
                )
            )

        return chunks

    def _fallback_chunk(self, content: str) -> list[ChunkInfo]:
        """Simple text-based chunking fallback.

        Splits on double newlines (paragraphs) and recombines to target size.
        """
        chunks = []
        lines = content.split("\n")

        current_chunk = []
        current_tokens = 0
        start_line = 1

        for i, line in enumerate(lines):
            line_tokens = len(line.split()) + 1  # Rough estimate

            if current_tokens + line_tokens > self.chunk_size and current_chunk:
                # Emit current chunk
                text = "\n".join(current_chunk)
                if len(text.strip()) > 0:
                    chunks.append(
                        ChunkInfo(
                            text=text,
                            token_count=current_tokens,
                            chunk_index=len(chunks),
                            start_line=start_line,
                            end_line=i,
                        )
                    )
                current_chunk = [line]
                current_tokens = line_tokens
                start_line = i + 1
            else:
                current_chunk.append(line)
                current_tokens += line_tokens

        # Emit final chunk
        if current_chunk:
            text = "\n".join(current_chunk)
            if len(text.strip()) > 0:
                chunks.append(
                    ChunkInfo(
                        text=text,
                        token_count=current_tokens,
                        chunk_index=len(chunks),
                        start_line=start_line,
                        end_line=len(lines),
                    )
                )

        return chunks
