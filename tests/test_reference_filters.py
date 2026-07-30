"""Usage filters must be observably distinct and truncation must be visible.

Regression cover for rq-lookupHonesty01.1 and .2. Previously the
``production_first`` and ``include_tests`` branches applied identical sort
keys, so ``include_tests`` was a dead branch: production occurrences sorted
first and filled the cap, leaving test occurrences permanently unreachable.
"""

from __future__ import annotations

import pytest

from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_references import MAX_REFERENCE_RESULTS, search_references

PROD_CALLS = MAX_REFERENCE_RESULTS + 25
TEST_CALLS = 4
# The test module's own ``from core import greet`` is an occurrence too.
TEST_OCCURRENCES = TEST_CALLS + 1


@pytest.fixture
def oversubscribed_repo(tmp_path):
    """A repo whose production occurrences alone exceed the result cap."""
    src = tmp_path / "src"
    tests_dir = tmp_path / "tests"
    src.mkdir()
    tests_dir.mkdir()

    (src / "core.py").write_text("def greet(name):\n    return name\n")

    lines = ["from core import greet", "", "def run():"]
    lines += [f'    greet("p{i}")' for i in range(PROD_CALLS)]
    (src / "prod.py").write_text("\n".join(lines) + "\n")

    test_lines = ["from core import greet", "", "def test_it():"]
    test_lines += [f'    greet("t{i}")' for i in range(TEST_CALLS)]
    (tests_dir / "test_greet.py").write_text("\n".join(test_lines) + "\n")

    index_folder(str(tmp_path), storage_dir=str(tmp_path / ".idx"))
    return tmp_path


def _run(repo, **kw):
    return search_references(
        "greet",
        str(repo),
        storage_dir=str(repo / ".idx"),
        limit=MAX_REFERENCE_RESULTS,
        **kw,
    )


def _test_rows(result):
    return [r for r in result["results"] if r.get("is_test_file")]


def test_include_tests_returns_test_rows_despite_cap(oversubscribed_repo):
    """The whole point of the filter: asking for tests returns tests."""
    result = _run(oversubscribed_repo, usage_filter="include_tests")

    assert _test_rows(result), (
        "include_tests returned no test occurrences even though the repo has "
        f"{TEST_CALLS}; production occurrences filled the entire cap."
    )


def test_three_filters_are_observably_distinct(oversubscribed_repo):
    """production_first, include_tests and tests_only must differ."""
    prod = _run(oversubscribed_repo, usage_filter="production_first")
    both = _run(oversubscribed_repo, usage_filter="include_tests")
    only = _run(oversubscribed_repo, usage_filter="tests_only")

    ids = [tuple(r["id"] for r in res["results"]) for res in (prod, both, only)]
    assert len(set(ids)) == 3, "the three usage filters returned duplicate result sets"


def test_production_first_ordering_is_unchanged(oversubscribed_repo):
    """Existing callers must not see production_first change behavior."""
    result = _run(oversubscribed_repo, usage_filter="production_first")

    assert not _test_rows(result), (
        "production_first should still fill the cap with production occurrences"
    )


def test_counts_make_truncation_visible(oversubscribed_repo):
    """A caller must be able to tell results were cut off, and by how much."""
    result = _run(oversubscribed_repo, usage_filter="include_tests")

    for field in (
        "production_matches",
        "test_matches",
        "returned_production",
        "returned_tests",
    ):
        assert field in result, f"response is missing the {field!r} count"

    assert result["production_matches"] >= PROD_CALLS
    assert result["test_matches"] == TEST_OCCURRENCES
    assert result["returned_production"] + result["returned_tests"] == len(result["results"])
    assert result["returned_production"] < result["production_matches"], (
        "this fixture is meant to be truncated"
    )


def test_reserved_slice_does_not_waste_capacity(oversubscribed_repo):
    """Unused test reserve returns to production, so the cap stays full."""
    result = _run(oversubscribed_repo, usage_filter="include_tests")

    assert len(result["results"]) == MAX_REFERENCE_RESULTS
    assert result["returned_tests"] == TEST_OCCURRENCES, (
        "all available test rows should fit inside the reserve"
    )


def test_tests_only_unchanged(oversubscribed_repo):
    result = _run(oversubscribed_repo, usage_filter="tests_only")

    assert result["results"]
    assert all(r.get("is_test_file") for r in result["results"])
