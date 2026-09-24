"""Code chunking for lgrep using Chonkie.

Uses AST-aware chunking via tree-sitter for better semantic boundaries.
"""

from __future__ import annotations

import re
from bisect import bisect_right
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

# chonkie's experimental CodeChunker (add_split_context=True, the default)
# prepends the enclosing node's header to chunks of a split node:
# ``header + "\n\n" + body`` for the first chunk and
# ``header + "\n\n\t...\n\n" + body`` for later chunks. The ``\t...``
# breadcrumb is synthetic and never appears in real source.
_BREADCRUMB = "\n\t...\n"

# First lines that look like a declaration header chonkie would inject.
# Kept tight: a wrong match strips real body lines from snippets.
_DECLARATION_RE = re.compile(
    r"^(?:async\s+)?(?:def|class|fn|func|function|impl|struct|trait|enum)\b"
)


def strip_split_breadcrumb(text: str) -> str:
    """Return chunk text with chonkie's breadcrumb-form header removed.

    Later chunks of a split node carry ``header + "\\n\\n\\t...\\n\\n"``
    in front of the body. The ``\\t...`` breadcrumb never appears in real
    source, so this strip is unambiguous from the stored text alone.
    Text without a breadcrumb is returned unchanged.
    """
    while True:
        idx = text.find(_BREADCRUMB)
        if idx < 0:
            return text
        text = text[idx + len(_BREADCRUMB) :].lstrip()


def strip_injected_class_header(text: str) -> str:
    """Return a candidate chunk body with chonkie's injected header removed.

    Removes the breadcrumb form (see :func:`strip_split_breadcrumb`) and
    then the plain ``header\\n\\n`` form that chonkie puts on the first
    chunk of a split node: stripped when the first line looks like a
    declaration and the line after the blank separator starts at column
    0 (a body chonkie lstripped).

    The plain form cannot be told apart from verbatim text with the same
    shape — two adjacent definitions separated by one blank line — so
    the result is only a candidate. :func:`locate_chunk_body` uses it
    only after the full chunk text is not found in the file, and only
    accepts it when the candidate body is found there.
    """
    text = strip_split_breadcrumb(text)
    head, sep, body = text.partition("\n\n")
    if sep:
        first_body_line = body.split("\n", 1)[0]
        if first_body_line and not first_body_line[0].isspace() and _DECLARATION_RE.match(head):
            return body
    return text


def _find_forward(content: str, needle: str, search_from: int) -> int:
    """Find ``needle`` at or after ``search_from``, else anywhere; -1 if absent."""
    pos = content.find(needle, search_from)
    if pos < 0 and search_from:
        pos = content.find(needle)
    return pos


def locate_chunk_body(content: str, chunk_text: str, search_from: int = 0) -> tuple[int, str]:
    """Locate a chunk's body in file content, header context stripped.

    The full chunk text is searched first, so a verbatim chunk maps to
    its own extent. Only when the full text is not in the file — the
    signature of chonkie's injected header context — is the header
    stripped and the full body searched. Matching whole text, never a
    prefix, keeps an injected header from matching its own declaration
    line. ``search_from`` moves the search forward so later chunks
    cannot match earlier file positions; the fallback searches from the
    start for out-of-order chunks.

    Returns ``(char_offset, body)``. ``char_offset`` is ``-1`` when the
    body is not found; ``body`` is the located text with any injected
    header removed.
    """
    pos = _find_forward(content, chunk_text, search_from)
    if pos >= 0:
        return pos, chunk_text
    body = strip_injected_class_header(chunk_text)
    if body != chunk_text:
        pos = _find_forward(content, body, search_from)
        if pos >= 0:
            return pos, body
    return -1, body


def compute_line_starts(content: str) -> list[int]:
    """Return the char offset at which each 1-indexed line starts.

    The returned list has one entry per line plus a trailing sentinel,
    so ``bisect_right`` over it maps any in-bounds offset to its line.
    """
    starts = [0]
    for line in content.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    return starts


def line_range_at(line_starts: list[int], start_pos: int, end_pos: int) -> tuple[int, int]:
    """Map a ``[start_pos, end_pos)`` char range to 1-indexed lines.

    Returns ``(start_line, end_line)``, both inclusive.
    """
    start_line = bisect_right(line_starts, start_pos)
    end_line = bisect_right(line_starts, max(start_pos, end_pos - 1))
    return start_line, max(start_line, end_line)


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

        Filters out tiny chunks and calculates line numbers. Chonkie
        injects header context in front of split chunk bodies, so each
        body is located through :func:`locate_chunk_body`, searching
        forward from the previous chunk. Stored chunk text keeps the
        injected header — it carries embedding context; only the line
        range is computed from the body.
        """
        chunks = []
        line_starts = compute_line_starts(content)
        cursor = 0

        for raw in raw_chunks:
            text = raw.text.strip()
            token_count = getattr(raw, "token_count", len(text.split()))

            # Skip tiny/empty chunks
            if token_count < MIN_CHUNK_TOKENS or not text:
                continue

            pos, body = locate_chunk_body(content, text, cursor)
            start_line = 1
            end_line = 1
            if pos >= 0:
                cursor = pos + 1
                start_line, end_line = line_range_at(line_starts, pos, pos + len(body))
            else:
                log.debug("chunk_body_not_located", chunk_index=len(chunks))

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
