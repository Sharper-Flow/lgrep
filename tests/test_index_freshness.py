"""Freshness-gate tests for the persisted symbol index.

The gate must never fire on an unchanged repository, whatever its size:
on a repo whose index walk was truncated by max_files the walked file set
can never equal the indexed set, so un-indexed additions are only
staleness for a complete index. mtime drift of indexed files and
confirmed on-disk deletions must still trigger the incremental refresh,
and a legacy index without the truncation marker must refresh once and
settle once the marker lands.

Tests here stay deterministic across filesystem directory orders (the
walk window of a truncated repo depends on os.walk order): assertions
target gate behavior and persisted state, and the deletion victim is
chosen from the persisted index's own file set, never by name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lgrep.storage.index_store import IndexStore, normalize_repo_key

if TYPE_CHECKING:
    from pathlib import Path

MAX_FILES = 500


def _make_repo(root: Path, count: int) -> Path:
    """Create a flat Python repo with *count* one-function modules."""
    src = root / "src"
    src.mkdir(exist_ok=True)
    for i in range(count):
        (src / f"m{i:03d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    return root


@pytest.fixture
def repo(tmp_path):
    return _make_repo(tmp_path, MAX_FILES + 5)


@pytest.fixture
def store_dir(tmp_path):
    return tmp_path / "symbol_store"


def _index(repo: Path, store_dir: Path) -> dict:
    from lgrep.tools.index_folder import index_folder

    result = index_folder(str(repo), storage_dir=store_dir)
    assert "error" not in result
    return result


def _index_files(repo: Path) -> set[str]:
    """The language files FileDiscovery sees, relative to *repo*."""
    from lgrep.discovery import FileDiscovery
    from lgrep.parser.languages import get_language_spec

    return {
        str(file_path.relative_to(repo))
        for file_path in FileDiscovery(repo).find_files()
        if get_language_spec(file_path.suffix.lower()) is not None
    }


def _spy_saves(monkeypatch) -> list[str]:
    """Replace IndexStore.save with a recording pass-through."""
    saves: list[str] = []
    real_save = IndexStore.save

    def spy(store_self, index):
        saves.append(index.repo_path)
        real_save(store_self, index)

    monkeypatch.setattr(IndexStore, "save", spy)
    return saves


class TestIndexIsBehindDecisionTable:
    """Unit coverage of the gate's branches, independent of index_folder."""

    @pytest.fixture
    def gate(self):
        from lgrep.tools._index_freshness import _index_is_behind

        return _index_is_behind

    @staticmethod
    def _quiet_mtime(tmp_path: Path) -> float:
        """An indexed_at newer than every file, so only the set branch can fire."""
        return tmp_path.stat().st_mtime + 1000

    def test_truncated_index_additions_only_do_not_fire(self, tmp_path, gate):
        _make_repo(tmp_path, 6)
        indexed = {
            "src/m000.py": "h",
            "src/m001.py": "h",
            "src/m002.py": "h",
            "src/m003.py": "h",
        }
        assert gate(tmp_path, indexed, self._quiet_mtime(tmp_path), walk_truncated=True) is False

    def test_complete_index_same_additions_fire(self, tmp_path, gate):
        _make_repo(tmp_path, 6)
        indexed = {
            "src/m000.py": "h",
            "src/m001.py": "h",
            "src/m002.py": "h",
            "src/m003.py": "h",
        }
        assert gate(tmp_path, indexed, self._quiet_mtime(tmp_path), walk_truncated=False) is True

    def test_truncated_index_confirmed_deletion_fires(self, tmp_path, gate):
        src = tmp_path / "src"
        src.mkdir()
        (src / "present.py").write_text("def present():\n    pass\n")
        indexed = {"src/present.py": "h", "src/m004.py": "h"}  # m004.py is gone
        assert gate(tmp_path, indexed, self._quiet_mtime(tmp_path), walk_truncated=True) is True

    def test_truncated_index_mtime_drift_fires(self, tmp_path, gate):
        _make_repo(tmp_path, 6)
        indexed = {rel: "h" for rel in _index_files(tmp_path)}
        assert gate(tmp_path, indexed, 0.0, walk_truncated=True) is True

    def test_matching_sets_never_fire(self, tmp_path, gate):
        _make_repo(tmp_path, 3)
        indexed = {rel: "h" for rel in _index_files(tmp_path)}
        future = tmp_path.stat().st_mtime + 1000
        assert gate(tmp_path, indexed, future, walk_truncated=False) is False


class TestUnchangedTruncatedRepoNoRefresh:
    """Check (a): a repo past max_files must not re-index or re-save."""

    def test_unchanged_truncated_repo_no_refresh_no_save(self, repo, store_dir, monkeypatch):
        result = _index(repo, store_dir)
        assert result["files_indexed"] == MAX_FILES  # the walk really truncated

        # Query a symbol the persisted index actually holds: which files
        # landed in the truncated window depends on filesystem walk order.
        index = IndexStore(storage_dir=store_dir).load(normalize_repo_key(str(repo)))
        assert index is not None
        query_symbol = next(iter(index.symbols.values()))["name"]

        saves = _spy_saves(monkeypatch)

        from lgrep.tools.search_symbols import search_symbols

        for _ in range(2):
            query = search_symbols(query_symbol, str(repo), storage_dir=store_dir)
            assert "error" not in query
            assert query["index_refreshed"] is False
            assert query["total_matches"] >= 1

        assert saves == []

    def test_persisted_index_carries_truncation_marker(self, repo, store_dir):
        _index(repo, store_dir)
        index = IndexStore(storage_dir=store_dir).load(normalize_repo_key(str(repo)))
        assert index is not None
        assert index.walk_truncated is True


class TestDriftStillRefreshes:
    """Check (b): indexed-file mtime drift and deletion still refresh."""

    def test_truncated_index_mtime_drift_still_refreshes(self, repo, store_dir, monkeypatch):
        from lgrep.tools.search_symbols import search_symbols

        _index(repo, store_dir)

        # Rewrite a file the persisted index holds: window membership
        # depends on filesystem walk order, the index does not.
        index = IndexStore(storage_dir=store_dir).load(normalize_repo_key(str(repo)))
        assert index is not None
        drifted = next(iter(index.files))
        (repo / drifted).write_text("def drifted_symbol():\n    return 1\n")

        first = search_symbols("drifted_symbol", str(repo), storage_dir=store_dir)
        assert "error" not in first
        assert first["index_refreshed"] is True

        # The refresh must settle: the second query re-saves nothing.
        saves = _spy_saves(monkeypatch)
        second = search_symbols("drifted_symbol", str(repo), storage_dir=store_dir)
        assert "error" not in second
        assert second["index_refreshed"] is False
        assert saves == []

    def test_truncated_index_deletion_still_refreshes(self, repo, store_dir, monkeypatch):
        from lgrep.tools.search_symbols import search_symbols

        _index(repo, store_dir)

        # Delete a file the index actually holds: window membership depends
        # on walk order, the persisted index does not.
        index = IndexStore(storage_dir=store_dir).load(normalize_repo_key(str(repo)))
        assert index is not None
        victim = next(iter(index.files))
        victim_symbol = next(
            sym["name"] for sym in index.symbols.values() if sym["file_path"] == victim
        )
        assert search_symbols(victim_symbol, str(repo), storage_dir=store_dir)["total_matches"] == 1

        (repo / victim).unlink()

        first = search_symbols(victim_symbol, str(repo), storage_dir=store_dir)
        assert "error" not in first
        assert first["index_refreshed"] is True
        # The deleted file's symbols are pruned despite the truncated walk.
        assert first["total_matches"] == 0

        # Converged: the next query neither refreshes nor saves.
        saves = _spy_saves(monkeypatch)
        second = search_symbols(victim_symbol, str(repo), storage_dir=store_dir)
        assert "error" not in second
        assert second["index_refreshed"] is False
        assert saves == []


class TestCompleteIndexAdditionStillRefreshes:
    """Check (c): the marker must not suppress genuine additions."""

    def test_new_file_refreshes_complete_index(self, tmp_path, store_dir):
        repo = _make_repo(tmp_path, 3)
        _index(repo, store_dir)

        (repo / "src" / "newmod.py").write_text("def brand_new_symbol():\n    return 42\n")

        from lgrep.tools.search_symbols import search_symbols

        result = search_symbols("brand_new_symbol", str(repo), storage_dir=store_dir)
        assert "error" not in result
        assert result["index_refreshed"] is True
        assert result["total_matches"] == 1


class TestLegacyIndexWithoutMarker:
    """An index saved before the marker refreshes once, then settles."""

    def test_additions_fire_once_then_settle(self, repo, store_dir, monkeypatch):
        from lgrep.tools.search_symbols import search_symbols

        _index(repo, store_dir)
        index = IndexStore(storage_dir=store_dir).load(normalize_repo_key(str(repo)))
        assert index is not None
        query_symbol = next(iter(index.symbols.values()))["name"]

        # Simulate a legacy body: strip the marker from the persisted JSON
        # and drop the in-process cache so the next load re-parses it.
        index_file = IndexStore(storage_dir=store_dir)._index_path(normalize_repo_key(str(repo)))
        data = json.loads(index_file.read_text(encoding="utf-8"))
        data.pop("walk_truncated", None)
        index_file.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        IndexStore._cache.clear()

        saves = _spy_saves(monkeypatch)

        first = search_symbols(query_symbol, str(repo), storage_dir=store_dir)
        assert "error" not in first
        assert first["index_refreshed"] is True
        assert len(saves) == 1  # the marker lands with this save

        saves.clear()
        second = search_symbols(query_symbol, str(repo), storage_dir=store_dir)
        assert "error" not in second
        assert second["index_refreshed"] is False
        assert saves == []
