"""Tests for code chunking."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from lgrep.chunking import (
    DEFAULT_CHUNK_SIZE,
    ChunkInfo,
    CodeChunker,
    CodeChunkResult,
    compute_line_starts,
    detect_language,
    line_range_at,
    locate_chunk_body,
    strip_injected_class_header,
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


class TestJsonTextRouting:
    """JSON routes to the text chunker; chonkie's AST constructor is never tried."""

    @staticmethod
    def _patch_chonkie(monkeypatch, **mock_kwargs):
        import chonkie.experimental

        constructor = Mock(**{"wraps": chonkie.experimental.CodeChunker, **mock_kwargs})
        monkeypatch.setattr(chonkie.experimental, "CodeChunker", constructor)
        return constructor

    def test_json_skips_ast_constructor_and_uses_fallback(self, tmp_path, monkeypatch):
        """No chonkie construction, no warning, json metadata, fallback chunks."""
        constructor = self._patch_chonkie(monkeypatch)
        path = tmp_path / "data.json"
        path.write_text('{\n  "a": 1,\n  "b": 2\n}', encoding="utf-8")
        chunker = CodeChunker(chunk_size=6)

        with capture_logs() as events:
            results = [
                chunker.chunk_file(path),
                chunker.chunk_file(path, path.read_text(encoding="utf-8")),
            ]

        assert constructor.call_count == 0
        assert [e for e in events if e["event"] == "chunker_creation_failed"] == []
        assert results[0].chunks == results[1].chunks
        for result in results:
            assert result.language == "json"
            assert result.error is None
            assert [(c.text, c.start_line, c.end_line) for c in result.chunks] == [
                ('{\n  "a": 1,', 1, 2),
                ('  "b": 2\n}', 3, 4),
            ]

    def test_json_chunks_equal_unknown_extension_chunks(self):
        """The json result is the same text chunking an unmapped extension gets."""
        content = '{\n  "a": 1,\n  "b": 2,\n  "c": 3\n}\n'
        as_json = CodeChunker(chunk_size=6).chunk_file("data.json", content)
        as_text = CodeChunker(chunk_size=6).chunk_file("data.unknownext", content)
        assert as_json.language == "json"
        assert as_json.chunks == as_text.chunks

    def test_empty_json_stays_empty(self):
        """Empty and whitespace-only JSON behave as any other empty file."""
        for content in ["", " \n\t "]:
            result = CodeChunker().chunk_file("empty.json", content)
            assert result.language is None
            assert result.chunks == []
            assert result.error is None

    def test_json_stays_watcher_eligible(self):
        """.json keeps its LANGUAGE_MAP entry and detected language."""
        from lgrep.watcher import SUPPORTED_EXTENSIONS

        assert ".json" in SUPPORTED_EXTENSIONS
        assert detect_language("data.json") == "json"
        assert detect_language("data.JSON") == "json"

    def test_supported_language_constructor_cached_across_calls(self, monkeypatch):
        """Non-JSON AST chunking constructs once and repeats identically."""
        constructor = self._patch_chonkie(monkeypatch)
        chunker = CodeChunker()
        source = "def compute_answer():\n    return 42\n"
        results = [chunker.chunk_file("sample.py", source) for _ in range(2)]
        assert constructor.call_count == 1
        assert constructor.call_args.kwargs == {"language": "python", "chunk_size": 500}
        assert results[0].chunks == results[1].chunks
        assert all(result.chunks and result.language == "python" for result in results)

    def test_unexpected_constructor_failure_still_warns(self, monkeypatch):
        """An unexpected constructor error warns per call and is not cached."""
        constructor = self._patch_chonkie(
            monkeypatch, side_effect=RuntimeError("unexpected constructor failure")
        )
        chunker = CodeChunker()
        source = "def compute_answer():\n    return 42\n"

        with capture_logs() as events:
            results = [chunker.chunk_file("sample.py", source) for _ in range(2)]

        assert constructor.call_count == 2
        failures = [e for e in events if e["event"] == "chunker_creation_failed"]
        assert len(failures) == 2
        assert all(e["error"] == "unexpected constructor failure" for e in failures)
        assert all(result.chunks and result.error is None for result in results)


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


class TestInjectedHeaderStripping:
    """strip_injected_class_header removes chonkie's context, not the body."""

    def test_breadcrumb_form_is_stripped(self):
        """Later split chunks carry header + breadcrumb; body survives."""
        text = "class Greeter:\n\n\t...\n\ndef greet(self, name):\n        return name"
        assert strip_injected_class_header(text).startswith("def greet")

    def test_plain_header_form_is_stripped(self):
        """First split chunks carry header + blank line; body survives."""
        text = "class Greeter:\n\ndef greet(self, name):\n        return name"
        assert strip_injected_class_header(text).startswith("def greet")

    def test_header_with_docstring_is_stripped(self):
        """chonkie's header may include the docstring."""
        text = 'class Greeter:\n    """Doc."""\n\ndef greet(self, name):\n        return name'
        assert strip_injected_class_header(text).startswith("def greet")

    def test_verbatim_import_block_survives(self):
        """A verbatim top-level chunk must not lose its leading lines."""
        text = "import os\n\ndef read_config():\n    return os.environ"
        assert strip_injected_class_header(text) == text

    def test_verbatim_class_body_survives(self):
        """A verbatim class chunk keeps its indented body."""
        text = "class Greeter:\n    def greet(self):\n        return 1"
        assert strip_injected_class_header(text) == text


class TestLocateChunkBody:
    """locate_chunk_body finds the body at its own file position."""

    def test_locates_verbatim_chunk_whole(self):
        content = "import os\n\ndef read_config():\n    return os.environ\n"
        chunk = "def read_config():\n    return os.environ"
        pos, body = locate_chunk_body(content, chunk)
        assert pos == content.find("def read_config")
        assert body == chunk

    def test_strips_header_and_locates_body(self):
        content = (
            'class Greeter:\n    """Doc."""\n\n    def greet(self, name):\n        return name\n'
        )
        chunk = 'class Greeter:\n    """Doc."""\n\ndef greet(self, name):\n        return name'
        pos, body = locate_chunk_body(content, chunk)
        assert content[pos:].startswith("def greet")
        assert body.startswith("def greet")

    def test_search_from_skips_earlier_occurrence(self):
        content = "def a():\n    return 1\n\n\ndef b():\n    return 1\n"
        first, _ = locate_chunk_body(content, "    return 1", 0)
        second, _ = locate_chunk_body(content, "    return 1", first + 1)
        assert first == content.find("    return 1")
        assert second == content.rfind("    return 1")
        assert second > first

    def test_unlocatable_body_returns_negative_offset(self):
        content = "def a():\n    return 1\n"
        pos, _ = locate_chunk_body(content, "def missing():\n    return 2\n")
        assert pos == -1


class TestLongInjectedHeader:
    """An injected header longer than any prefix probe must not match its
    own declaration line in the file."""

    HEADER = "class ServiceWithAVeryLongDescriptiveName(BaseServiceWithMixins):"
    SOURCE = (
        HEADER + "\n"  # 1
        '    """Service."""\n'  # 2
        "    limit = 1\n"  # 3
        "    def run(self):\n"  # 4
        "        return self.limit\n"  # 5
    )

    @pytest.mark.parametrize("separator", ["\n\n\t...\n\n", "\n\n"])
    def test_body_is_located_not_the_declaration(self, separator):
        from lgrep.chunking import compute_line_starts, line_range_at, locate_chunk_body

        assert len(self.HEADER) > 50
        chunk = self.HEADER + separator + "def run(self):\n        return self.limit"

        pos, body = locate_chunk_body(self.SOURCE, chunk)

        assert body.startswith("def run(self):")
        start, end = line_range_at(compute_line_starts(self.SOURCE), pos, pos + len(body))
        assert (start, end) == (4, 5)


class TestLineRangeAt:
    """compute_line_starts + line_range_at map char offsets to lines."""

    def test_maps_offsets_to_lines(self):
        content = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        starts = compute_line_starts(content)
        pos = content.find("def b():")
        assert line_range_at(starts, pos, pos + len("def b():\n    return 2")) == (4, 5)

    def test_single_line_chunk(self):
        content = "def a():\n    return 1\n"
        starts = compute_line_starts(content)
        pos = content.find("    return 1")
        assert line_range_at(starts, pos, pos + len("    return 1")) == (2, 2)


class TestMethodChunkLineRanges:
    """_process_chunks stores per-method ranges despite injected headers."""

    @staticmethod
    def _raw(text, tokens=30):
        return SimpleNamespace(text=text, token_count=tokens)

    def _content(self):
        return (
            '"""Module doc."""\n'  # 1
            "\n"  # 2
            "class Greeter:\n"  # 3
            '    """Class doc."""\n'  # 4
            "\n"  # 5
            "    def greet(self, name):\n"  # 6
            '        return f"Hello, {name}!"\n'  # 7
            "\n"  # 8
            "    def farewell(self, name):\n"  # 9
            '        return f"Goodbye, {name}!"\n'  # 10
            "\n"  # 11
            "\n"  # 12
            "def standalone():\n"  # 13
            "    return 42\n"  # 14
        )

    def test_method_chunks_get_their_own_ranges(self):
        content = self._content()
        chunks = [
            # First split chunk: header (with docstring) + blank line + lstripped body.
            self._raw(
                'class Greeter:\n    """Class doc."""\n\n'
                "def greet(self, name):\n        "
                'return f"Hello, {name}!"'
            ),
            # Later split chunk: header + breadcrumb + lstripped body.
            self._raw(
                "class Greeter:\n\n\t...\n\n"
                "def farewell(self, name):\n        "
                'return f"Goodbye, {name}!"'
            ),
            # Non-split chunk: verbatim text.
            self._raw("def standalone():\n    return 42"),
        ]
        result = CodeChunker()._process_chunks(chunks, content)

        assert [(c.start_line, c.end_line) for c in result] == [
            (6, 7),
            (9, 10),
            (13, 14),
        ]

    def test_stored_text_keeps_injected_header(self):
        """Stored chunk text is untouched — only the line range changes."""
        content = self._content()
        raw = self._raw(
            "class Greeter:\n\n\t...\n\n"
            "def farewell(self, name):\n        "
            'return f"Goodbye, {name}!"'
        )
        result = CodeChunker()._process_chunks([raw], content)
        assert result[0].text == raw.text.strip()
        assert (result[0].start_line, result[0].end_line) == (9, 10)

    def test_unlocatable_chunk_falls_back_to_defaults(self):
        content = self._content()
        raw = self._raw("def nowhere():\n    return 0")
        result = CodeChunker()._process_chunks([raw], content)
        assert (result[0].start_line, result[0].end_line) == (1, 1)
