"""Tests for CLI symbol subcommands and input validation.

Covers:
- lgrep search-symbols <query> [path]
- lgrep index-symbols [path]
- Input validation: empty query, invalid path, negative limit clamping
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── CLI dispatch ──────────────────────────────────────────────────────────────


class TestCLIDispatch:
    def test_search_symbols_dispatched(self, tmp_path):
        """lgrep search-symbols dispatches to _cmd_search_symbols."""
        from lgrep.cli import main

        with patch("lgrep.cli._cmd_search_symbols", return_value=0) as mock:
            with patch("sys.argv", ["lgrep", "search-symbols", "authenticate", str(tmp_path)]):
                result = main()
        mock.assert_called_once()
        assert result == 0

    def test_index_symbols_dispatched(self, tmp_path):
        """lgrep index-symbols dispatches to _cmd_index_symbols."""
        from lgrep.cli import main

        with patch("lgrep.cli._cmd_index_symbols", return_value=0) as mock:
            with patch("sys.argv", ["lgrep", "index-symbols", str(tmp_path)]):
                result = main()
        mock.assert_called_once()
        assert result == 0


class TestCLISearchSymbols:
    def test_help_exits_zero(self, capsys):
        from lgrep.cli import _cmd_search_symbols

        result = _cmd_search_symbols(["--help"])
        assert result == 0

    def test_no_query_exits_nonzero(self, capsys):
        from lgrep.cli import _cmd_search_symbols

        result = _cmd_search_symbols([])
        assert result != 0

    def test_search_symbols_outputs_json(self, tmp_path, capsys):
        from lgrep.cli import _cmd_search_symbols

        # Create a minimal repo and index it
        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")

        # First index it
        from lgrep.tools.index_folder import index_folder

        store_dir = tmp_path / ".lgrep_symbols"
        index_folder(str(tmp_path), storage_dir=store_dir)

        result = _cmd_search_symbols(["authenticate", str(tmp_path), f"--storage-dir={store_dir}"])
        assert result == 0
        captured = capsys.readouterr()
        # Find the JSON line (last non-empty line starting with '{')
        json_line = next(
            (l for l in reversed(captured.out.splitlines()) if l.strip().startswith("{")),
            None,
        )
        assert json_line is not None, f"No JSON line found in output: {captured.out!r}"
        data = json.loads(json_line)
        assert "results" in data

    def test_missing_index_outputs_error_json(self, tmp_path, capsys):
        from lgrep.cli import _cmd_search_symbols

        result = _cmd_search_symbols(["authenticate", str(tmp_path)])
        assert result != 0
        captured = capsys.readouterr()
        json_line = next(
            (l for l in reversed(captured.out.splitlines()) if l.strip().startswith("{")),
            None,
        )
        assert json_line is not None
        data = json.loads(json_line)
        assert "error" in data


class TestCLIIndexSymbols:
    def test_help_exits_zero(self, capsys):
        from lgrep.cli import _cmd_index_symbols

        result = _cmd_index_symbols(["--help"])
        assert result == 0

    def test_index_symbols_outputs_json(self, tmp_path, capsys):
        from lgrep.cli import _cmd_index_symbols

        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")
        store_dir = tmp_path / ".lgrep_symbols"

        result = _cmd_index_symbols([str(tmp_path), f"--storage-dir={store_dir}"])
        assert result == 0
        captured = capsys.readouterr()
        json_line = next(
            (l for l in reversed(captured.out.splitlines()) if l.strip().startswith("{")),
            None,
        )
        assert json_line is not None, f"No JSON line found in output: {captured.out!r}"
        data = json.loads(json_line)
        assert "symbols_indexed" in data

    def test_invalid_path_outputs_error_json(self, capsys):
        from lgrep.cli import _cmd_index_symbols

        result = _cmd_index_symbols(["/nonexistent/path/xyz"])
        assert result != 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data


# ── Input validation ──────────────────────────────────────────────────────────


class TestInputValidation:
    """Verify symbol tools validate inputs and return structured errors."""

    def test_search_symbols_empty_query_returns_error(self, tmp_path, tmp_path_factory):
        from lgrep.tools.search_symbols import search_symbols
        from lgrep.tools.index_folder import index_folder

        store = tmp_path_factory.mktemp("store")
        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")
        index_folder(str(tmp_path), storage_dir=store)

        result = search_symbols("", str(tmp_path), storage_dir=store)
        assert "error" in result

    def test_search_symbols_negative_limit_clamped(self, tmp_path, tmp_path_factory):
        from lgrep.tools.search_symbols import search_symbols
        from lgrep.tools.index_folder import index_folder

        store = tmp_path_factory.mktemp("store")
        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")
        index_folder(str(tmp_path), storage_dir=store)

        # Negative limit should be clamped to 1, not error
        result = search_symbols("authenticate", str(tmp_path), storage_dir=store, limit=-5)
        assert "error" not in result
        assert "results" in result

    def test_search_text_empty_query_returns_error(self, tmp_path):
        from lgrep.tools.search_text import search_text

        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")
        result = search_text("", str(tmp_path))
        assert "error" in result

    def test_get_symbol_empty_id_returns_error(self, tmp_path, tmp_path_factory):
        from lgrep.tools.get_symbol import get_symbol
        from lgrep.tools.index_folder import index_folder

        store = tmp_path_factory.mktemp("store")
        (tmp_path / "auth.py").write_text("def authenticate(): pass\n")
        index_folder(str(tmp_path), storage_dir=store)

        result = get_symbol("", str(tmp_path), storage_dir=store)
        assert "error" in result

    def test_index_folder_empty_path_returns_error(self):
        from lgrep.tools.index_folder import index_folder

        result = index_folder("")
        assert "error" in result

    def test_get_file_outline_nonexistent_file_returns_error(self):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline("/nonexistent/file.py")
        assert "error" in result

    def test_get_file_tree_nonexistent_path_returns_error(self):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree("/nonexistent/path/xyz")
        assert "error" in result

    def test_get_repo_outline_nonexistent_path_returns_error(self):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline("/nonexistent/path/xyz")
        assert "error" in result
