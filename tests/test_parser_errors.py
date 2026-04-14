"""Tests for parser error handling (tk-G1vnpyBD).

RED phase: tests fail before error handling is implemented.
GREEN phase: all pass after implementation.

Verifies graceful handling of:
- Malformed/unparseable files (tree-sitter parse failures)
- Unsupported language files (no LanguageSpec match)
- Files with encoding errors
- Empty files
- Files that don't exist
"""

import pytest


class TestUnsupportedLanguage:
    """Unsupported file extensions must return empty list, not crash."""

    def test_unsupported_extension_returns_empty(self, tmp_path):
        """Files with unsupported extensions must return []."""
        from lgrep.parser.extractor import SymbolExtractor

        unknown_file = tmp_path / "data.xyz"
        unknown_file.write_text("some content")

        extractor = SymbolExtractor()
        result = extractor.extract(unknown_file)

        assert result == [], f"Expected [], got {result}"

    def test_binary_extension_returns_empty(self, tmp_path):
        """Binary file extensions must return []."""
        from lgrep.parser.extractor import SymbolExtractor

        binary_file = tmp_path / "compiled.so"
        binary_file.write_bytes(b"\x7fELF\x02\x01\x01\x00" * 10)

        extractor = SymbolExtractor()
        result = extractor.extract(binary_file)

        assert result == []


class TestEmptyFile:
    """Empty files must return empty list, not crash."""

    def test_empty_python_file_returns_empty(self, tmp_path):
        """Empty .py file must return []."""
        from lgrep.parser.extractor import SymbolExtractor

        empty_file = tmp_path / "empty.py"
        empty_file.write_text("")

        extractor = SymbolExtractor()
        result = extractor.extract(empty_file)

        assert result == []

    def test_whitespace_only_file_returns_empty(self, tmp_path):
        """Whitespace-only file must return []."""
        from lgrep.parser.extractor import SymbolExtractor

        ws_file = tmp_path / "whitespace.py"
        ws_file.write_text("   \n\n\t\n")

        extractor = SymbolExtractor()
        result = extractor.extract(ws_file)

        assert result == []


class TestMissingFile:
    """Missing files must return empty list, not crash."""

    def test_nonexistent_file_returns_empty(self, tmp_path):
        """Non-existent file must return []."""
        from lgrep.parser.extractor import SymbolExtractor

        missing = tmp_path / "does_not_exist.py"

        extractor = SymbolExtractor()
        result = extractor.extract(missing)

        assert result == []


class TestEncodingErrors:
    """Files with encoding errors must return empty list, not crash."""

    def test_invalid_utf8_returns_empty_or_partial(self, tmp_path):
        """Files with invalid UTF-8 must not crash (return [] or partial results)."""
        from lgrep.parser.extractor import SymbolExtractor

        bad_file = tmp_path / "bad_encoding.py"
        # Write valid Python with an invalid UTF-8 byte sequence in a comment
        bad_file.write_bytes(b"def foo():\n    # \xff\xfe invalid utf8\n    pass\n")

        extractor = SymbolExtractor()
        # Must not raise — return [] or partial results
        try:
            result = extractor.extract(bad_file)
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"SymbolExtractor.extract() raised {type(e).__name__}: {e}")


class TestMalformedCode:
    """Malformed/syntactically invalid code must return partial results, not crash."""

    def test_malformed_python_does_not_crash(self, tmp_path):
        """Malformed Python must not crash — tree-sitter is error-tolerant."""
        from lgrep.parser.extractor import SymbolExtractor

        malformed = tmp_path / "malformed.py"
        malformed.write_text("def foo(\n    # unclosed paren\n    pass\n")

        extractor = SymbolExtractor()
        # Must not raise
        try:
            result = extractor.extract(malformed)
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"SymbolExtractor.extract() raised {type(e).__name__}: {e}")

    def test_syntax_error_python_does_not_crash(self, tmp_path):
        """Python with syntax errors must not crash."""
        from lgrep.parser.extractor import SymbolExtractor

        bad_syntax = tmp_path / "bad_syntax.py"
        bad_syntax.write_text("def foo():\n    return (\n")

        extractor = SymbolExtractor()
        try:
            result = extractor.extract(bad_syntax)
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"SymbolExtractor.extract() raised {type(e).__name__}: {e}")


class TestNeverCrashContract:
    """SymbolExtractor.extract() must NEVER raise an exception."""

    @pytest.mark.parametrize(
        "content,filename",
        [
            (b"", "empty.py"),
            (b"\x00\x01\x02\x03", "binary.py"),
            (b"def foo(", "truncated.py"),
            (b"class A:\n" * 1000, "deep_nesting.py"),
            (b"# just a comment\n", "comment_only.py"),
            (b"x = 1\ny = 2\n", "no_symbols.py"),
        ],
    )
    def test_never_raises(self, tmp_path, content, filename):
        """SymbolExtractor must never raise for any input."""
        from lgrep.parser.extractor import SymbolExtractor

        test_file = tmp_path / filename
        test_file.write_bytes(content)

        extractor = SymbolExtractor()
        try:
            result = extractor.extract(test_file)
            assert isinstance(result, list)
        except Exception as e:
            pytest.fail(f"SymbolExtractor.extract({filename!r}) raised {type(e).__name__}: {e}")
