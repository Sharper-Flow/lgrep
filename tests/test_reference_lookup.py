"""Tests for bounded candidate reference lookup (lgrep_search_references).

RED phase: tests fail before the lookup tool and MCP handler exist.
GREEN phase: tests pass after implementation.

Covers:
- Happy-path retrieval of candidate occurrences with enclosing context
- Production-first, include-tests, and tests-only filtering
- Ambiguous name handling and candidate disclaimers
- Kind filtering
- Empty/invalid inputs, unindexed repo, and stale index without occurrence data
- Limit enforcement
- Exclusion of definition names from occurrence results
"""

from __future__ import annotations

import textwrap

import pytest


@pytest.fixture
def reference_repo(tmp_path):
    """Create a small Python repo with production and test usages of a name."""
    src = tmp_path / "src"
    src.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()

    (src / "service.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"Service module.\"\"\"

            from helpers import helper


            class UserService:
                \"\"\"Service for users.\"\"\"

                def work(self):
                    return helper(value=1)
            """
        )
    )

    (src / "helpers.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"Helpers.\"\"\"

            def helper(value):
                return value + 1


            def another():
                # reference the same name in a different production file
                return helper(2)
            """
        )
    )

    (tests / "test_service.py").write_text(
        textwrap.dedent(
            """\
            \"\"\"Tests for service.\"\"\"

            from helpers import helper


            def test_helper():
                assert helper(0) == 1
            """
        )
    )

    return tmp_path


@pytest.fixture
def store_dir(tmp_path):
    """Return a temporary storage directory for the symbol index."""
    return tmp_path / "symbol_store"


class TestSearchReferences:
    def test_tool_importable(self):
        from lgrep.tools.search_references import search_references  # noqa: F401

    def test_index_and_retrieve_occurrences(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references("helper", str(reference_repo), storage_dir=store_dir)

        assert "error" not in result
        assert result["query"] == "helper"
        assert result["total_matches"] > 0
        assert result["results"]
        assert result["candidate_names"] == ["helper"]
        assert "not compiler-accurate" in result["disclaimer"].lower()
        assert "exhaustive" in result["disclaimer"].lower()

        for occ in result["results"]:
            assert "id" in occ
            assert "name" in occ
            assert "file_path" in occ
            assert "line_number" in occ
            assert "line_text" in occ
            assert "kind" in occ
            assert "enclosing_symbol_id" in occ
            assert "is_test_file" in occ

    def test_results_include_enclosing_context(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references("helper", str(reference_repo), storage_dir=store_dir)

        call_results = [o for o in result["results"] if o["kind"] == "call"]
        assert call_results
        assert any("UserService.work" in (o["enclosing_symbol_id"] or "") for o in call_results)

    def test_production_first_ordering(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references(
            "helper", str(reference_repo), storage_dir=store_dir, usage_filter="production_first"
        )

        assert "error" not in result
        files = [o["file_path"] for o in result["results"]]
        # Production files should appear before the test file
        test_indices = [i for i, f in enumerate(files) if "tests/" in f]
        prod_indices = [i for i, f in enumerate(files) if "tests/" not in f]
        if test_indices and prod_indices:
            assert max(prod_indices) < min(test_indices)

    def test_include_tests_filter(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references(
            "helper", str(reference_repo), storage_dir=store_dir, usage_filter="include_tests"
        )

        assert "error" not in result
        assert any(o["is_test_file"] for o in result["results"])
        assert any(not o["is_test_file"] for o in result["results"])

    def test_tests_only_filter(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references(
            "helper", str(reference_repo), storage_dir=store_dir, usage_filter="tests_only"
        )

        assert "error" not in result
        assert result["results"]
        assert all(o["is_test_file"] for o in result["results"])

    def test_kind_filter(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references(
            "helper", str(reference_repo), storage_dir=store_dir, kind="call"
        )

        assert "error" not in result
        assert result["results"]
        assert all(o["kind"] == "call" for o in result["results"])

    def test_ambiguous_name_disclaimer(self, reference_repo, store_dir):
        # helper is defined once but used in multiple production files and tests.
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references("helper", str(reference_repo), storage_dir=store_dir)

        assert "error" not in result
        assert result["total_matches"] > 1
        assert "candidate" in result["disclaimer"].lower()

    def test_definitions_excluded_from_occurrences(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references("helper", str(reference_repo), storage_dir=store_dir)

        assert "error" not in result
        # The definition line `def helper(value):` should not be an occurrence
        for occ in result["results"]:
            assert "def helper" not in occ["line_text"]

    def test_limit_enforced(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references("helper", str(reference_repo), storage_dir=store_dir, limit=1)

        assert "error" not in result
        assert len(result["results"]) == 1
        assert result["total_matches"] > 1

    def test_limit_is_capped_to_bound_response_work(self, tmp_path, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import MAX_REFERENCE_RESULTS, search_references

        source = tmp_path / "many_uses.py"
        source.write_text("\n".join("helper()" for _ in range(MAX_REFERENCE_RESULTS + 1)))

        index_folder(str(tmp_path), storage_dir=store_dir)
        result = search_references("helper", str(tmp_path), storage_dir=store_dir, limit=10_000)

        assert "error" not in result
        assert result["total_matches"] == MAX_REFERENCE_RESULTS + 1
        assert len(result["results"]) == MAX_REFERENCE_RESULTS

    def test_empty_query_error(self, store_dir):
        from lgrep.tools.search_references import search_references

        result = search_references("", "/not/used", storage_dir=store_dir)
        assert "error" in result

    def test_invalid_usage_filter_error(self, reference_repo, store_dir):
        from lgrep.tools.search_references import search_references

        result = search_references(
            "helper", str(reference_repo), storage_dir=store_dir, usage_filter="invalid"
        )
        assert "error" in result

    def test_unindexed_repo_error(self, reference_repo, store_dir):
        from lgrep.tools.search_references import search_references

        result = search_references("helper", str(reference_repo), storage_dir=store_dir)
        assert "error" in result

    def test_stale_index_without_occurrences_error(self, reference_repo, store_dir):
        from lgrep.storage.index_store import CodeIndex, IndexStore
        from lgrep.tools.search_references import search_references

        repo_key = str(reference_repo.resolve())
        store = IndexStore(storage_dir=store_dir)
        stale_index = CodeIndex(
            repo_path=repo_key,
            files={"src/service.py": "deadbeef"},
            symbols={},
            occurrences={},
            version="2.0",
        )
        store.save(stale_index)

        result = search_references("helper", str(reference_repo), storage_dir=store_dir)
        assert "error" in result
        assert "occurrence" in result["error"].lower()

    def test_no_matches_returns_empty_safe_result(self, reference_repo, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        index_folder(str(reference_repo), storage_dir=store_dir)
        result = search_references(
            "definitely_not_present", str(reference_repo), storage_dir=store_dir
        )

        assert "error" not in result
        assert result["results"] == []
        assert result["total_matches"] == 0
        assert result["candidate_names"] == []


class TestSearchReferencesFileClassification:
    def test_conftest_classified_as_test(self, tmp_path, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        src = tmp_path / "src"
        src.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()

        (src / "lib.py").write_text("def shared(): pass\n")
        (tests / "conftest.py").write_text("from lib import shared\ndef fixture(): shared()\n")

        index_folder(str(tmp_path), storage_dir=store_dir)
        result = search_references(
            "shared", str(tmp_path), storage_dir=store_dir, usage_filter="tests_only"
        )

        assert "error" not in result
        assert result["results"]
        assert all(o["is_test_file"] for o in result["results"])

    def test_test_prefix_suffix_classified(self, tmp_path, store_dir):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        src = tmp_path / "src"
        src.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()

        (src / "lib.py").write_text("def shared(): pass\n")
        (tests / "test_lib.py").write_text("from lib import shared\ndef test_x(): shared()\n")
        (src / "lib_test.py").write_text("from lib import shared\ndef x(): shared()\n")

        index_folder(str(tmp_path), storage_dir=store_dir)
        result = search_references(
            "shared", str(tmp_path), storage_dir=store_dir, usage_filter="tests_only"
        )

        paths = [o["file_path"] for o in result["results"]]
        assert any("tests/test_lib.py" in p for p in paths)
        assert any("lib_test.py" in p for p in paths)
