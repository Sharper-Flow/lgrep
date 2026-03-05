"""Contract tests for all 11 new symbol tools.

Tests cover:
- Happy path for each tool
- Missing index error (repo not indexed)
- Invalid repo error
- _meta envelope presence and field shapes
- search_symbols result field shapes
- get_symbol source retrieval
- get_symbols batch
- get_file_outline structure
- get_repo_outline structure
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

# ── Import contracts ──────────────────────────────────────────────────────────
# Each tool module must be importable from lgrep.tools.*


def test_tools_package_importable():
    import lgrep.tools  # noqa: F401


def test_index_folder_importable():
    from lgrep.tools.index_folder import index_folder  # noqa: F401


def test_index_repo_importable():
    from lgrep.tools.index_repo import index_repo  # noqa: F401


def test_list_repos_importable():
    from lgrep.tools.list_repos import list_repos  # noqa: F401


def test_get_file_tree_importable():
    from lgrep.tools.get_file_tree import get_file_tree  # noqa: F401


def test_get_file_outline_importable():
    from lgrep.tools.get_file_outline import get_file_outline  # noqa: F401


def test_get_repo_outline_importable():
    from lgrep.tools.get_repo_outline import get_repo_outline  # noqa: F401


def test_search_symbols_importable():
    from lgrep.tools.search_symbols import search_symbols  # noqa: F401


def test_search_text_importable():
    from lgrep.tools.search_text import search_text  # noqa: F401


def test_get_symbol_importable():
    from lgrep.tools.get_symbol import get_symbol  # noqa: F401


def test_get_symbols_importable():
    from lgrep.tools.get_symbol import get_symbols  # noqa: F401


def test_invalidate_cache_importable():
    from lgrep.tools.invalidate_cache import invalidate_cache  # noqa: F401


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal Python repo with one source file."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        'def authenticate(user, password):\n    """Authenticate a user."""\n    return True\n\nclass AuthManager:\n    def login(self, user):\n        pass\n'
    )
    (src / "utils.py").write_text("def helper():\n    pass\n")
    return tmp_path


@pytest.fixture
def tmp_store(tmp_path):
    """Return a temp storage dir for IndexStore."""
    return tmp_path / "symbol_store"


# ── index_folder ──────────────────────────────────────────────────────────────


class TestIndexFolder:
    def test_returns_dict(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_meta_envelope(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "_meta" in result

    def test_meta_has_timing(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "timing_ms" in result["_meta"]
        assert isinstance(result["_meta"]["timing_ms"], (int, float))

    def test_meta_has_tokens_saved(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "tokens_saved" in result["_meta"]

    def test_reports_indexed_files(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "files_indexed" in result
        assert result["files_indexed"] >= 1

    def test_reports_symbol_count(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "symbols_indexed" in result
        assert result["symbols_indexed"] >= 1

    def test_invalid_path_returns_error(self, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder("/nonexistent/path/xyz", storage_dir=tmp_store)
        assert "error" in result

    def test_repo_path_in_result(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert "repo_path" in result


# ── list_repos ────────────────────────────────────────────────────────────────


class TestListRepos:
    def test_returns_dict(self, tmp_store):
        from lgrep.tools.list_repos import list_repos

        result = list_repos(storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_repos_key(self, tmp_store):
        from lgrep.tools.list_repos import list_repos

        result = list_repos(storage_dir=tmp_store)
        assert "repos" in result

    def test_empty_when_no_index(self, tmp_store):
        from lgrep.tools.list_repos import list_repos

        result = list_repos(storage_dir=tmp_store)
        assert result["repos"] == []

    def test_shows_indexed_repo(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.list_repos import list_repos

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = list_repos(storage_dir=tmp_store)
        assert str(tmp_repo) in result["repos"]

    def test_has_meta_envelope(self, tmp_store):
        from lgrep.tools.list_repos import list_repos

        result = list_repos(storage_dir=tmp_store)
        assert "_meta" in result


# ── get_file_tree ─────────────────────────────────────────────────────────────


class TestGetFileTree:
    def test_returns_dict(self, tmp_repo):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree(str(tmp_repo))
        assert isinstance(result, dict)

    def test_has_files_key(self, tmp_repo):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree(str(tmp_repo))
        assert "files" in result

    def test_files_is_list(self, tmp_repo):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree(str(tmp_repo))
        assert isinstance(result["files"], list)

    def test_finds_python_files(self, tmp_repo):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree(str(tmp_repo))
        # Should find at least the .py files we created
        assert any(f.endswith(".py") for f in result["files"])

    def test_has_meta_envelope(self, tmp_repo):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree(str(tmp_repo))
        assert "_meta" in result

    def test_invalid_path_returns_error(self):
        from lgrep.tools.get_file_tree import get_file_tree

        result = get_file_tree("/nonexistent/path/xyz")
        assert "error" in result


# ── get_file_outline ──────────────────────────────────────────────────────────


class TestGetFileOutline:
    def test_returns_dict(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        assert isinstance(result, dict)

    def test_has_symbols_key(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        assert "symbols" in result

    def test_symbols_is_list(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        assert isinstance(result["symbols"], list)

    def test_finds_function(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        names = [s["name"] for s in result["symbols"]]
        assert "authenticate" in names

    def test_finds_class(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        names = [s["name"] for s in result["symbols"]]
        assert "AuthManager" in names

    def test_symbol_has_required_fields(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        sym = result["symbols"][0]
        assert "id" in sym
        assert "name" in sym
        assert "kind" in sym

    def test_has_meta_envelope(self, tmp_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(tmp_repo / "src" / "auth.py"))
        assert "_meta" in result

    def test_invalid_path_returns_error(self):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline("/nonexistent/file.py")
        assert "error" in result


# ── get_repo_outline ──────────────────────────────────────────────────────────


class TestGetRepoOutline:
    def test_returns_dict(self, tmp_repo):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline(str(tmp_repo))
        assert isinstance(result, dict)

    def test_has_files_key(self, tmp_repo):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline(str(tmp_repo))
        assert "files" in result

    def test_has_total_symbols(self, tmp_repo):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline(str(tmp_repo))
        assert "total_symbols" in result
        assert result["total_symbols"] >= 1

    def test_has_meta_envelope(self, tmp_repo):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline(str(tmp_repo))
        assert "_meta" in result

    def test_invalid_path_returns_error(self):
        from lgrep.tools.get_repo_outline import get_repo_outline

        result = get_repo_outline("/nonexistent/path/xyz")
        assert "error" in result


# ── search_symbols ────────────────────────────────────────────────────────────


class TestSearchSymbols:
    def test_returns_dict(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_results_key(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        assert "results" in result

    def test_finds_matching_symbol(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        names = [r["name"] for r in result["results"]]
        assert "authenticate" in names

    def test_result_has_required_fields(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        assert len(result["results"]) > 0
        sym = result["results"][0]
        assert "id" in sym
        assert "name" in sym
        assert "kind" in sym
        assert "file_path" in sym

    def test_has_meta_envelope(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        assert "_meta" in result

    def test_missing_index_returns_error(self, tmp_repo, tmp_store):
        from lgrep.tools.search_symbols import search_symbols

        result = search_symbols("authenticate", str(tmp_repo), storage_dir=tmp_store)
        assert "error" in result

    def test_no_match_returns_empty_list(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = search_symbols("zzz_no_such_symbol_xyz", str(tmp_repo), storage_dir=tmp_store)
        assert result["results"] == []


# ── search_text ───────────────────────────────────────────────────────────────


class TestSearchText:
    def test_returns_dict(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("authenticate", str(tmp_repo))
        assert isinstance(result, dict)

    def test_has_results_key(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("authenticate", str(tmp_repo))
        assert "results" in result

    def test_finds_text_match(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("authenticate", str(tmp_repo))
        assert len(result["results"]) > 0

    def test_result_has_file_and_line(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("authenticate", str(tmp_repo))
        r = result["results"][0]
        assert "file_path" in r
        assert "line_number" in r
        assert "line" in r

    def test_has_meta_envelope(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("authenticate", str(tmp_repo))
        assert "_meta" in result

    def test_no_match_returns_empty_list(self, tmp_repo):
        from lgrep.tools.search_text import search_text

        result = search_text("zzz_no_such_text_xyz", str(tmp_repo))
        assert result["results"] == []

    def test_invalid_path_returns_error(self):
        from lgrep.tools.search_text import search_text

        result = search_text("anything", "/nonexistent/path/xyz")
        assert "error" in result


# ── get_symbol ────────────────────────────────────────────────────────────────


class TestGetSymbol:
    def test_returns_dict(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbol

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        sym_id = f"src/auth.py:function:authenticate"
        result = get_symbol(sym_id, str(tmp_repo), storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_symbol_key(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbol

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        sym_id = f"src/auth.py:function:authenticate"
        result = get_symbol(sym_id, str(tmp_repo), storage_dir=tmp_store)
        assert "symbol" in result

    def test_symbol_has_source(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbol

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        sym_id = f"src/auth.py:function:authenticate"
        result = get_symbol(sym_id, str(tmp_repo), storage_dir=tmp_store)
        assert "source" in result["symbol"]
        assert "authenticate" in result["symbol"]["source"]

    def test_has_meta_envelope(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbol

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        sym_id = f"src/auth.py:function:authenticate"
        result = get_symbol(sym_id, str(tmp_repo), storage_dir=tmp_store)
        assert "_meta" in result

    def test_missing_index_returns_error(self, tmp_repo, tmp_store):
        from lgrep.tools.get_symbol import get_symbol

        result = get_symbol(
            "src/auth.py:function:authenticate", str(tmp_repo), storage_dir=tmp_store
        )
        assert "error" in result

    def test_unknown_symbol_returns_error(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbol

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = get_symbol(
            "src/auth.py:function:nonexistent_xyz", str(tmp_repo), storage_dir=tmp_store
        )
        assert "error" in result


# ── get_symbols (batch) ───────────────────────────────────────────────────────


class TestGetSymbols:
    def test_returns_dict(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        ids = ["src/auth.py:function:authenticate", "src/auth.py:class:AuthManager"]
        result = get_symbols(ids, str(tmp_repo), storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_symbols_key(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        ids = ["src/auth.py:function:authenticate"]
        result = get_symbols(ids, str(tmp_repo), storage_dir=tmp_store)
        assert "symbols" in result

    def test_returns_multiple_symbols(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        ids = ["src/auth.py:function:authenticate", "src/auth.py:class:AuthManager"]
        result = get_symbols(ids, str(tmp_repo), storage_dir=tmp_store)
        assert len(result["symbols"]) == 2

    def test_has_meta_envelope(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.get_symbol import get_symbols

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        ids = ["src/auth.py:function:authenticate"]
        result = get_symbols(ids, str(tmp_repo), storage_dir=tmp_store)
        assert "_meta" in result

    def test_missing_index_returns_error(self, tmp_repo, tmp_store):
        from lgrep.tools.get_symbol import get_symbols

        result = get_symbols(
            ["src/auth.py:function:authenticate"], str(tmp_repo), storage_dir=tmp_store
        )
        assert "error" in result


# ── invalidate_cache ──────────────────────────────────────────────────────────


class TestInvalidateCache:
    def test_returns_dict(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = invalidate_cache(str(tmp_repo), storage_dir=tmp_store)
        assert isinstance(result, dict)

    def test_has_status_key(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = invalidate_cache(str(tmp_repo), storage_dir=tmp_store)
        assert "status" in result

    def test_removes_index(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache
        from lgrep.tools.list_repos import list_repos

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        assert str(tmp_repo) in list_repos(storage_dir=tmp_store)["repos"]

        invalidate_cache(str(tmp_repo), storage_dir=tmp_store)
        assert str(tmp_repo) not in list_repos(storage_dir=tmp_store)["repos"]

    def test_has_meta_envelope(self, tmp_repo, tmp_store):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache

        index_folder(str(tmp_repo), storage_dir=tmp_store)
        result = invalidate_cache(str(tmp_repo), storage_dir=tmp_store)
        assert "_meta" in result

    def test_nonexistent_repo_returns_ok(self, tmp_store):
        from lgrep.tools.invalidate_cache import invalidate_cache

        # Invalidating a non-indexed repo should not error
        result = invalidate_cache("/some/path/not/indexed", storage_dir=tmp_store)
        assert "error" not in result or result.get("status") == "not_found"
