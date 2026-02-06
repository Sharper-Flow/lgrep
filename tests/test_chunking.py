"""Tests for code chunking."""

import tempfile
from pathlib import Path

import pytest

from lgrep.chunking import (
    DEFAULT_CHUNK_SIZE,
    CodeChunker,
    CodeChunkResult,
    ChunkInfo,
    detect_language,
)


class TestDetectLanguage:
    """Tests for language detection."""

    def test_python_files(self):
        """Should detect Python files."""
        assert detect_language("test.py") == "python"
        assert detect_language("module.pyi") == "python"
        assert detect_language("/path/to/file.py") == "python"

    def test_javascript_files(self):
        """Should detect JavaScript files."""
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"

    def test_typescript_files(self):
        """Should detect TypeScript files."""
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_rust_files(self):
        """Should detect Rust files."""
        assert detect_language("lib.rs") == "rust"

    def test_unknown_extension(self):
        """Should return None for unknown extensions."""
        assert detect_language("file.xyz") is None
        assert detect_language("noextension") is None

    def test_case_insensitive(self):
        """Should handle mixed case extensions."""
        assert detect_language("file.PY") == "python"
        assert detect_language("file.Js") == "javascript"


class TestChunkInfo:
    """Tests for ChunkInfo dataclass."""

    def test_create_chunk_info(self):
        """Should create chunk info with all fields."""
        info = ChunkInfo(
            text="def test(): pass",
            token_count=10,
            chunk_index=0,
            start_line=1,
            end_line=5,
        )
        assert info.text == "def test(): pass"
        assert info.token_count == 10
        assert info.chunk_index == 0
        assert info.start_line == 1
        assert info.end_line == 5


class TestCodeChunker:
    """Tests for CodeChunker class."""

    @pytest.fixture
    def chunker(self):
        """Create a CodeChunker instance."""
        return CodeChunker(chunk_size=500)

    def test_init_default_chunk_size(self):
        """Should use default chunk size."""
        chunker = CodeChunker()
        assert chunker.chunk_size == DEFAULT_CHUNK_SIZE

    def test_init_custom_chunk_size(self):
        """Should accept custom chunk size."""
        chunker = CodeChunker(chunk_size=256)
        assert chunker.chunk_size == 256

    def test_chunk_python_code(self, chunker):
        """Should chunk Python code using AST."""
        code = '''
import os

def hello():
    """Say hello."""
    print("Hello, world!")

class Greeter:
    def greet(self, name):
        return f"Hello, {name}!"
'''
        result = chunker.chunk_file("test.py", code)

        assert result.file_path == "test.py"
        assert result.language == "python"
        assert result.error is None
        assert len(result.chunks) > 0

        # All chunks should have content
        for chunk in result.chunks:
            assert chunk.text.strip()
            assert chunk.token_count > 0

    def test_chunk_empty_file(self, chunker):
        """Should handle empty files."""
        result = chunker.chunk_file("test.py", "")
        assert result.chunks == []
        assert result.error is None

    def test_chunk_whitespace_only(self, chunker):
        """Should handle whitespace-only files."""
        result = chunker.chunk_file("test.py", "   \n\n   ")
        assert result.chunks == []

    def test_chunk_unknown_language(self, chunker):
        """Should fall back to text chunking for unknown languages."""
        content = "This is some text content.\nWith multiple lines.\nAnd more content."
        result = chunker.chunk_file("file.xyz", content)

        assert result.language is None
        assert len(result.chunks) > 0
        assert result.error is None

    def test_chunk_file_from_disk(self, chunker):
        """Should read and chunk file from disk."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def test():\n    return 42\n")
            temp_path = f.name

        try:
            result = chunker.chunk_file(temp_path)
            assert result.language == "python"
            assert len(result.chunks) > 0
        finally:
            Path(temp_path).unlink()

    def test_chunk_missing_file(self, chunker):
        """Should return error for missing files."""
        result = chunker.chunk_file("/nonexistent/file.py")
        assert result.error is not None
        assert "Failed to read file" in result.error

    def test_chunks_have_line_numbers(self, chunker):
        """Should include line numbers in chunks."""
        code = """
def first_function():
    return 1

def second_function():
    return 2
"""
        result = chunker.chunk_file("test.py", code)

        for chunk in result.chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line


class TestCodeChunkResult:
    """Tests for CodeChunkResult dataclass."""

    def test_create_result(self):
        """Should create result with all fields."""
        result = CodeChunkResult(
            file_path="test.py",
            chunks=[ChunkInfo("code", 10, 0, 1, 5)],
            language="python",
        )
        assert result.file_path == "test.py"
        assert len(result.chunks) == 1
        assert result.language == "python"
        assert result.error is None

    def test_create_error_result(self):
        """Should create error result."""
        result = CodeChunkResult(
            file_path="test.py",
            error="File not found",
        )
        assert result.error == "File not found"
        assert result.chunks == []
