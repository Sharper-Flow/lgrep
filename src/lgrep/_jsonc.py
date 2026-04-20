"""JSONC (JSON with Comments) loader and writer.

JSONC is JSON extended with JavaScript-style line comments (//) and block
comments (/* */). It is the format used by lgrep's install config file.

This module provides string-literal-aware parsing: comment delimiters that
appear inside quoted string values are treated as literal text, not as
comment boundaries.

Adversarial cases handled:
  - URLs like "https://example.com" — the // must not start a line comment
  - Strings containing "/*" or "*/" — must not close/open a block comment early
  - "http://foo//bar" — both // are inside a string, both are literal
"""

from __future__ import annotations

import json
import re
from typing import Any


def load_jsonc_text(text: str) -> dict[str, Any]:
    """Load a JSONC (JSON with comments) string and return a plain dict.

    Strips:
      - Line comments: ``//`` to end of line (outside string literals)
      - Block comments: ``/* */`` (outside string literals)
      - Trailing commas before ``]`` or ``}``

    Args:
        text: JSONC-formatted string.

    Returns:
        Parsed Python dict.

    Raises:
        ValueError: If the stripped text is not valid JSON.
    """
    stripped = _strip_comments(text)
    stripped = _strip_trailing_commas(stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSONC (after stripping comments): {exc}") from exc


def dump_jsonc_text(data: dict[str, Any], *, indent: int | None = None) -> str:
    """Dump a dict to a JSON string.

    This is a thin wrapper around ``json.dumps`` — no comment-preservation
    is needed for write operations.

    Args:
        data: Python dict to serialize.
        indent: Pass an int to pretty-print.

    Returns:
        JSON string (no added comments — install config uses plain JSON).
    """
    return json.dumps(data, indent=indent, separators=(",", ": "))


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments that are outside string literals.

    Uses a character-by-character scan that tracks whether the cursor is
    inside a single-quoted or double-quoted string. Comment markers inside
    strings are treated as literal characters.

    Correctly handles all adversarial cases including:
      - "https://example.com" — // is inside a string
      - "http://foo//bar"     — both // are inside a string
      - "/* not a comment */" — /* and */ are inside a string
      - "// also not a comment"
    """
    result: list[str] = []
    i = 0
    n = len(text)
    in_string: str | None = None  # None, '"', or "'"

    while i < n:
        c = text[i]

        # Enter a string?
        if in_string is None and c in ('"', "'"):
            in_string = c
            result.append(c)
            i += 1
            continue

        # Exit a string?
        if in_string is not None and c == in_string:
            # In JSON, only double-quoted strings use backslash escapes.
            # Single-quoted strings are not valid JSON but we handle them
            # for config-file robustness: a single ' is never escaped.
            if in_string == "'":
                in_string = None
            else:
                # Count preceding backslashes to determine if this " is escaped.
                # An odd number of \ before " means \" (escaped, stay in string).
                # An even number (including 0) means closing quote.
                num_backslashes = 0
                j = i - 1
                while j >= 0 and text[j] == "\\":
                    num_backslashes += 1
                    j -= 1
                if num_backslashes % 2 == 0:
                    in_string = None
            result.append(c)
            i += 1
            continue

        # Inside a string: copy character literally
        if in_string is not None:
            result.append(c)
            i += 1
            continue

        # Outside a string: check for comment markers
        # Line comment //
        if i + 1 < n and c == "/" and text[i + 1] == "/":
            # Skip to end of line or end of file
            j = text.find("\n", i)
            if j == -1:
                # Comment runs to EOF — discard everything remaining
                break
            i = j + 1
            continue

        # Block comment /* */
        if i + 1 < n and c == "/" and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j == -1:
                # Unclosed block comment — discard to EOF
                break
            i = j + 2
            continue

        # Any other character
        result.append(c)
        i += 1

    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ]."""
    return re.sub(r",(\s*[}\]])", r"\1", text)
