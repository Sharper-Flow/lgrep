"""Tests for Python occurrence indexing and safe old-index refresh.

RED phase: tests fail before occurrence extraction/indexing exists.
GREEN phase: all pass after implementation.

Covers:
- Occurrence dataclass shape and stable IDs
- Python AST candidate-occurrence extraction (imports, calls, attributes, references)
- Excluding definition names from occurrences
- Enclosing-symbol context for occurrences
- Index persistence and keyed lookup
- Safe refresh migration for old indexes that lack occurrence data
- Incremental skip behavior for indexes that already contain occurrences
"""

from __future__ import annotations

import textwrap

import pytest


@pytest.fixture
def occurrence_source(tmp_path):
    """Create a small Python repo with a variety of candidate occurrences."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "service.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"Service module.\"\"\"

            import os
            import json as js


            def helper(value):
                return value + 1


            class UserService:
                \"\"\"Service for users.\"\"\"

                def __init__(self):
                    self.count = 0

                def get_user(self, user_id: int):
                    data = helper(user_id)
                    return os.path.join("users", str(data))

                def load(self):
                    return js.loads("{}")
            """
        )
    )
    return tmp_path


@pytest.fixture
def tmp_store(tmp_path):
    """Return a temp storage dir for IndexStore."""
    return tmp_path / "symbol_store"


# ============================================================================
# Occurrence dataclass
# ============================================================================


class TestOccurrenceModel:
    def test_occurrence_importable(self):
        from lgrep.parser.symbols import Occurrence  # noqa: F401

    def test_occurrence_has_required_fields(self):
        from lgrep.parser.symbols import Occurrence

        occ = Occurrence(
            id="src/a.py:occurrence:foo:12",
            name="foo",
            file_path="src/a.py",
            start_byte=12,
            end_byte=15,
            line_number=2,
            line_text="    foo()",
            kind="call",
        )
        assert occ.name == "foo"
        assert occ.file_path == "src/a.py"
        assert occ.kind == "call"
        assert occ.line_number == 2

    def test_occurrence_optional_enclosing_symbol(self):
        from lgrep.parser.symbols import Occurrence

        occ = Occurrence(
            id="src/a.py:occurrence:foo:12",
            name="foo",
            file_path="src/a.py",
            start_byte=12,
            end_byte=15,
            line_number=2,
            line_text="    foo()",
            kind="call",
            enclosing_symbol_id="src/a.py:function:bar",
        )
        assert occ.enclosing_symbol_id == "src/a.py:function:bar"


# ============================================================================
# Python occurrence extraction
# ============================================================================


class TestPythonOccurrenceExtraction:
    def test_extract_full_returns_both_lists(self, occurrence_source):
        from lgrep.parser.extractor import SymbolExtractor

        extractor = SymbolExtractor()
        symbols, occurrences = extractor.extract_full(
            occurrence_source / "src" / "service.py", repo_root=occurrence_source
        )
        assert isinstance(symbols, list)
        assert isinstance(occurrences, list)
        assert len(symbols) > 0

    def test_occurrence_kinds_detected(self, occurrence_source):
        from lgrep.parser.extractor import SymbolExtractor

        extractor = SymbolExtractor()
        _, occurrences = extractor.extract_full(
            occurrence_source / "src" / "service.py", repo_root=occurrence_source
        )
        by_name_kind = {(o.name, o.kind) for o in occurrences}

        assert ("os", "import") in by_name_kind
        assert ("helper", "call") in by_name_kind
        assert ("js", "import") in by_name_kind
        assert ("loads", "attribute") in by_name_kind
        assert ("str", "call") in by_name_kind

    def test_occurrences_have_location_and_context(self, occurrence_source):
        from lgrep.parser.extractor import SymbolExtractor

        extractor = SymbolExtractor()
        _, occurrences = extractor.extract_full(
            occurrence_source / "src" / "service.py", repo_root=occurrence_source
        )
        helper_occ = next(o for o in occurrences if o.name == "helper" and o.kind == "call")
        assert helper_occ.line_number > 0
        assert "helper" in helper_occ.line_text
        assert helper_occ.start_byte < helper_occ.end_byte

    def test_occurrences_have_enclosing_symbol(self, occurrence_source):
        from lgrep.parser.extractor import SymbolExtractor

        extractor = SymbolExtractor()
        _, occurrences = extractor.extract_full(
            occurrence_source / "src" / "service.py", repo_root=occurrence_source
        )
        helper_occ = next(o for o in occurrences if o.name == "helper" and o.kind == "call")
        assert helper_occ.enclosing_symbol_id == "src/service.py:method:UserService.get_user"

    def test_definitions_excluded_from_occurrences(self, occurrence_source):
        from lgrep.parser.extractor import SymbolExtractor

        extractor = SymbolExtractor()
        _, occurrences = extractor.extract_full(
            occurrence_source / "src" / "service.py", repo_root=occurrence_source
        )
        names = {o.name for o in occurrences}
        # Function/class definition names are usages only when referenced elsewhere.
        assert "UserService" not in names
        assert "helper" in names  # referenced inside get_user


# ============================================================================
# Indexing and safe refresh migration
# ============================================================================


class TestOccurrenceIndexing:
    def test_index_folder_stores_occurrences(self, occurrence_source, tmp_store):
        from lgrep.storage.index_store import IndexStore
        from lgrep.tools.index_folder import index_folder

        result = index_folder(str(occurrence_source), storage_dir=tmp_store)
        assert "occurrences_indexed" in result
        assert result["occurrences_indexed"] > 0

        store = IndexStore(storage_dir=tmp_store)
        idx = store.load(str(occurrence_source.resolve()))
        assert idx is not None
        assert "helper" in idx.occurrences
        assert any(o["kind"] == "call" for o in idx.occurrences["helper"])

    def test_occurrences_keyed_by_name(self, occurrence_source, tmp_store):
        from lgrep.storage.index_store import IndexStore
        from lgrep.tools.index_folder import index_folder

        index_folder(str(occurrence_source), storage_dir=tmp_store)
        idx = IndexStore(storage_dir=tmp_store).load(str(occurrence_source.resolve()))
        assert idx is not None
        assert isinstance(idx.occurrences, dict)
        for name, occs in idx.occurrences.items():
            assert all(o["name"] == name for o in occs)

    def test_old_index_version_forces_occurrence_refresh(self, occurrence_source, tmp_store):
        """An index without occurrence data must be safely refreshed for all files."""
        from lgrep.storage.index_store import CodeIndex, IndexStore
        from lgrep.tools.index_folder import index_folder

        # 1. Build a new-style index with occurrences.
        first = index_folder(str(occurrence_source), storage_dir=tmp_store)
        assert first["occurrences_indexed"] > 0

        # 2. Mutate the stored index to look like an old (pre-occurrence) index.
        store = IndexStore(storage_dir=tmp_store)
        idx = store.load(str(occurrence_source.resolve()))
        assert idx is not None
        old_index = CodeIndex(
            repo_path=idx.repo_path,
            files=dict(idx.files),
            symbols=dict(idx.symbols),
            version="2.0",
            occurrences={},
        )
        store.save(old_index)

        # 3. Incremental re-run must detect the missing occurrence data and re-parse.
        second = index_folder(str(occurrence_source), storage_dir=tmp_store, incremental=True)
        assert second["files_skipped"] == 0
        assert second["occurrences_indexed"] > 0

        refreshed = store.load(str(occurrence_source.resolve()))
        assert refreshed is not None
        assert refreshed.version >= "2.1"
        assert "helper" in refreshed.occurrences

    def test_new_index_skips_unchanged_files(self, occurrence_source, tmp_store):
        from lgrep.storage.index_store import IndexStore
        from lgrep.tools.index_folder import index_folder

        first = index_folder(str(occurrence_source), storage_dir=tmp_store)
        assert first["occurrences_indexed"] > 0

        second = index_folder(str(occurrence_source), storage_dir=tmp_store, incremental=True)
        assert second["files_skipped"] >= 1
        assert second["occurrences_indexed"] == first["occurrences_indexed"]

        idx = IndexStore(storage_dir=tmp_store).load(str(occurrence_source.resolve()))
        assert idx is not None
        assert "helper" in idx.occurrences


@pytest.fixture
def go_source(tmp_path):
    """Create a small Go repo with plain-call and selector occurrences."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.go").write_text(
        textwrap.dedent(
            """\
            package main

            import "fmt"

            type Config struct{ Name string }

            func helper() { fmt.Println("x") }

            func main() {
                var c Config
                helper()
                fmt.Println(c.Name)
            }
            """
        )
    )
    return tmp_path


class TestGoOccurrenceIndexRefresh:
    """Consumer compatibility: indexes written before Go occurrence support
    (version 2.1, Python-occurrence era) contain Go symbols but zero Go
    occurrences. The version gate must force a safe re-parse so existing
    Go-containing indexes are not silently stuck without Go occurrences."""

    def test_go_21_index_forces_occurrence_refresh(self, go_source, tmp_store):
        from lgrep.storage.index_store import CodeIndex, IndexStore
        from lgrep.tools.index_folder import index_folder

        # 1. Index with current code, then downgrade the stored version to
        #    2.1 and strip occurrences — simulating an index written before
        #    Go occurrence support shipped.
        first = index_folder(str(go_source), storage_dir=tmp_store)
        assert first["occurrences_indexed"] > 0

        store = IndexStore(storage_dir=tmp_store)
        idx = store.load(str(go_source.resolve()))
        assert idx is not None
        old_index = CodeIndex(
            repo_path=idx.repo_path,
            files=dict(idx.files),
            symbols=dict(idx.symbols),
            version="2.1",
            occurrences={},
        )
        store.save(old_index)

        # 2. Incremental re-run must not skip the unchanged Go file — the
        #    pre-Go-occurrences version triggers a safe refresh.
        second = index_folder(str(go_source), storage_dir=tmp_store, incremental=True)
        assert second["files_skipped"] == 0

        refreshed = store.load(str(go_source.resolve()))
        assert refreshed is not None
        assert refreshed.version >= "2.2"
        assert "helper" in refreshed.occurrences
        assert "Println" in refreshed.occurrences
