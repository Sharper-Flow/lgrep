"""Tests for the JSONC (JSON with Comments) loader.

JSONC is JSON with JavaScript-style line comments (//) and block comments (/* */).
The loader must handle:
- Line comments that appear on their own line
- Line comments that appear after a value (trailing)
- Block comments between values
- Block comments at the end of a file
- Trailing commas
- Adversarial cases: // and ,/ inside string literals should NOT be treated as comments
"""

import textwrap

import pytest

from lgrep._jsonc import dump_jsonc_text, load_jsonc_text

# ============================================================================
# Happy path: valid JSONC constructs
# ============================================================================

class TestLoadJsoncComments:
    """JSONC comment-stripping behavior."""

    def test_line_comment_own_line(self):
        """// comment on its own line is stripped."""
        text = textwrap.dedent("""\
            {
                // this is a comment
                "key": "value"
            }
        """)
        assert load_jsonc_text(text) == {"key": "value"}

    def test_line_comment_after_value(self):
        """// comment after a value (trailing comment) is stripped."""
        text = textwrap.dedent("""\
            {
                "key": "value" // trailing comment
            }
        """)
        assert load_jsonc_text(text) == {"key": "value"}

    def test_multiple_line_comments(self):
        """Multiple // comments are all stripped."""
        text = textwrap.dedent("""\
            {
                // comment one
                "a": 1,
                // comment two
                "b": 2
            }
        """)
        assert load_jsonc_text(text) == {"a": 1, "b": 2}

    def test_block_comment_between_values(self):
        """/* block comment */ between values is stripped."""
        text = textwrap.dedent("""\
            {
                "a": 1,
                /* block comment */
                "b": 2
            }
        """)
        assert load_jsonc_text(text) == {"a": 1, "b": 2}

    def test_block_comment_at_end(self):
        """/* block comment */ at the end of the file is stripped."""
        text = '{"key": "value"} /* trailing block comment */'
        assert load_jsonc_text(text) == {"key": "value"}

    def test_block_comment_own_line(self):
        """/* block comment */ on its own line is stripped."""
        text = textwrap.dedent("""\
            {
                /* block comment */
                "key": "value"
            }
        """)
        assert load_jsonc_text(text) == {"key": "value"}

    def test_trailing_comma(self):
        """Trailing comma after last item is allowed."""
        text = '{"a": 1, "b": 2,}'
        assert load_jsonc_text(text) == {"a": 1, "b": 2}

    def test_comment_before_closing_brace(self):
        """Comment immediately before } does not break parsing."""
        text = '{"a": 1 // comment\n}'
        assert load_jsonc_text(text) == {"a": 1}

    def test_mixed_comments(self):
        """Mix of // and /* */ comments with trailing comma."""
        text = textwrap.dedent("""\
            {
                // header comment
                "name": "test",
                /* mid comment */
                "value": 42 // trailing
            }
        """)
        assert load_jsonc_text(text) == {"name": "test", "value": 42}


class TestLoadJsoncAdversarial:
    """Adversarial cases: // and ,/ inside string literals must NOT trigger comment stripping."""

    def test_double_slash_inside_string(self):
        r"""A URL like "https://example.com" must NOT have // treated as a comment."""
        text = '{"url": "https://example.com/path"}'
        assert load_jsonc_text(text) == {"url": "https://example.com/path"}

    def test_double_slash_at_start_of_value(self):
        r"""// at the start of a string value is literal, not a comment."""
        text = '{"key": "// not a comment"}'
        assert load_jsonc_text(text) == {"key": "// not a comment"}

    def test_adversarial_double_slash_in_string(self):
        r"""Adversarial case: "http://foo//bar" must stay intact."""
        text = '{"url": "http://foo//bar"}'
        assert load_jsonc_text(text) == {"url": "http://foo//bar"}

    def test_adversarial_block_end_in_string(self):
        r"""String containing "*/" must not close a block comment early."""
        text = '{"msg": "comment ends here */ and more"}'
        assert load_jsonc_text(text) == {"msg": "comment ends here */ and more"}

    def test_block_comment_containing_slash_star(self):
        r"""A string containing "/*" or "*/" must not be treated as a comment delimiter."""
        text = '{"msg": "value /* not a block comment */ end"}'
        assert load_jsonc_text(text) == {"msg": "value /* not a block comment */ end"}

    def test_double_slash_in_multiline_string(self):
        r"""// inside a multiline string is literal text.

        In JSON, strings with embedded newlines must use \n escape sequences.
        The // is inside a quoted string value, so it must stay literal.
        """
        # Use \n escape sequence (valid JSON), not a literal newline char
        text = '{"log": "line one\\n// not a comment\\nline two"}'
        assert load_jsonc_text(text) == {"log": "line one\n// not a comment\nline two"}


class TestLoadJsoncMalformed:
    """Malformed inputs should raise informative errors."""

    def test_unclosed_bracket(self):
        with pytest.raises(ValueError):
            load_jsonc_text('{"key": "unclosed"')

    def test_invalid_syntax(self):
        with pytest.raises(ValueError):
            load_jsonc_text('{"key": ::}')

    def test_single_slash_not_comment(self):
        """A single / without a second / or * is just a literal character."""
        text = '{"key": "/"}'
        assert load_jsonc_text(text) == {"key": "/"}


class TestDumpJsoncText:
    """dump_jsonc_text should produce parseable JSON (no round-trip comments added)."""

    def test_dumps_valid_json(self):
        data = {"name": "test", "value": 42}
        result = dump_jsonc_text(data)
        assert load_jsonc_text(result) == data

    def test_dumps_with_special_chars(self):
        data = {"url": "https://example.com/path"}
        result = dump_jsonc_text(data)
        assert load_jsonc_text(result) == data

    def test_dumps_unicode(self):
        data = {"emoji": "🚀", "unicode": "日本語"}
        result = dump_jsonc_text(data)
        assert load_jsonc_text(result) == data

    def test_dumps_preserves_exact_round_trip(self):
        data = {"a": 1, "b": "two"}
        round_tripped = load_jsonc_text(dump_jsonc_text(data))
        assert round_tripped == data
