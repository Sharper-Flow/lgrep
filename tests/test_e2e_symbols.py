"""E2E integration tests for symbol workflows.

Tests the full pipeline:
- lgrep_index_symbols_folder → lgrep_search_symbols → lgrep_get_symbol
- lgrep_get_file_outline structure
- lgrep_invalidate_cache → full re-index
- Stable ID regression across two index runs
"""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_repo(tmp_path):
    """Create a sample Python repo with multiple files."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "auth.py").write_text(
        "def authenticate(user, password):\n"
        '    """Authenticate a user."""\n'
        "    return True\n\n"
        "class AuthManager:\n"
        "    def login(self, user):\n"
        '        """Log in a user."""\n'
        "        pass\n\n"
        "    def logout(self, user):\n"
        "        pass\n"
    )
    (src / "utils.py").write_text(
        'def helper():\n    pass\n\ndef format_error(msg):\n    return f"Error: {msg}"\n'
    )
    return tmp_path


@pytest.fixture
def store_dir(tmp_path):
    return tmp_path / "symbol_store"


class TestIndexSearchGetPipeline:
    """Full pipeline: index → search → get_symbol."""

    def test_index_then_search_finds_symbol(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        result = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)

        assert "error" not in result
        assert len(result["results"]) > 0
        names = [r["name"] for r in result["results"]]
        assert "authenticate" in names

    def test_index_then_get_symbol_returns_source(self, sample_repo, store_dir):
        from lgrep.tools.get_symbol import get_symbol
        from lgrep.tools.index_folder import index_folder

        index_folder(str(sample_repo), storage_dir=store_dir)
        sym_id = "src/auth.py:function:authenticate"
        result = get_symbol(sym_id, str(sample_repo), storage_dir=store_dir)

        assert "error" not in result
        assert "symbol" in result
        assert result["symbol"]["source"] is not None
        assert "authenticate" in result["symbol"]["source"]

    def test_index_then_get_symbols_batch(self, sample_repo, store_dir):
        from lgrep.tools.get_symbol import get_symbols
        from lgrep.tools.index_folder import index_folder

        index_folder(str(sample_repo), storage_dir=store_dir)
        ids = [
            "src/auth.py:function:authenticate",
            "src/auth.py:class:AuthManager",
        ]
        result = get_symbols(ids, str(sample_repo), storage_dir=store_dir)

        assert "error" not in result
        assert len(result["symbols"]) == 2
        names = [s["name"] for s in result["symbols"]]
        assert "authenticate" in names
        assert "AuthManager" in names

    def test_search_finds_method(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        result = search_symbols("login", str(sample_repo), storage_dir=store_dir)

        assert "error" not in result
        names = [r["name"] for r in result["results"]]
        assert "login" in names

    def test_search_kind_filter(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        result = search_symbols("", str(sample_repo), storage_dir=store_dir, kind="class")
        # Empty query should return error (validation)
        assert "error" in result

        # Valid kind filter
        result = search_symbols("Auth", str(sample_repo), storage_dir=store_dir, kind="class")
        assert "error" not in result
        for r in result["results"]:
            assert r["kind"] == "class"


class TestFileOutlineStructure:
    """lgrep_get_file_outline returns correct structure."""

    def test_outline_has_all_symbols(self, sample_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(sample_repo / "src" / "auth.py"))

        assert "error" not in result
        names = [s["name"] for s in result["symbols"]]
        assert "authenticate" in names
        assert "AuthManager" in names
        assert "login" in names

    def test_outline_symbol_kinds(self, sample_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(sample_repo / "src" / "auth.py"))
        kinds = {s["name"]: s["kind"] for s in result["symbols"]}

        assert kinds["authenticate"] == "function"
        assert kinds["AuthManager"] == "class"
        assert kinds["login"] == "method"

    def test_outline_symbol_has_id(self, sample_repo):
        from lgrep.tools.get_file_outline import get_file_outline

        result = get_file_outline(str(sample_repo / "src" / "auth.py"))
        for sym in result["symbols"]:
            assert "id" in sym
            assert ":" in sym["id"]  # file:kind:name format

    def test_outline_no_index_needed(self, sample_repo):
        """get_file_outline works without prior indexing."""
        from lgrep.tools.get_file_outline import get_file_outline

        # No index_folder call — should still work
        result = get_file_outline(str(sample_repo / "src" / "auth.py"))
        assert "error" not in result
        assert result["symbol_count"] > 0


class TestInvalidateCacheAndReindex:
    """lgrep_invalidate_cache → full re-index."""

    def test_invalidate_removes_index(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache
        from lgrep.tools.list_repos import list_repos

        index_folder(str(sample_repo), storage_dir=store_dir)
        assert str(sample_repo.resolve()) in list_repos(storage_dir=store_dir)["repos"]

        invalidate_cache(str(sample_repo), storage_dir=store_dir)
        assert str(sample_repo.resolve()) not in list_repos(storage_dir=store_dir)["repos"]

    def test_search_after_invalidate_returns_error(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        invalidate_cache(str(sample_repo), storage_dir=store_dir)

        result = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)
        assert "error" in result

    def test_reindex_after_invalidate_works(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.invalidate_cache import invalidate_cache
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        invalidate_cache(str(sample_repo), storage_dir=store_dir)
        index_folder(str(sample_repo), storage_dir=store_dir)  # re-index

        result = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)
        assert "error" not in result
        assert len(result["results"]) > 0


class TestStableIDRegression:
    """Symbol IDs are stable across two index runs."""

    def test_ids_stable_across_reindex(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        # First index
        index_folder(str(sample_repo), storage_dir=store_dir)
        result1 = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)
        ids1 = {r["id"] for r in result1["results"]}

        # Second index (same files, no changes)
        index_folder(str(sample_repo), storage_dir=store_dir)
        result2 = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)
        ids2 = {r["id"] for r in result2["results"]}

        assert ids1 == ids2, f"IDs changed across re-index: {ids1} vs {ids2}"

    def test_id_format_is_file_kind_name(self, sample_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        index_folder(str(sample_repo), storage_dir=store_dir)
        result = search_symbols("authenticate", str(sample_repo), storage_dir=store_dir)

        for sym in result["results"]:
            parts = sym["id"].split(":")
            assert len(parts) == 3, f"ID should be file:kind:name, got: {sym['id']}"
            file_path, kind, name = parts
            assert file_path.endswith(".py")
            assert kind in ("function", "class", "method", "interface", "symbol")
            assert name == sym["name"]
