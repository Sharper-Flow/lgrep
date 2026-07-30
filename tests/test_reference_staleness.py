"""Returned occurrences must disclose whether their backing file has changed.

Regression cover for rq-lookupHonesty01.3. Previously lookup returned stored
line numbers and text verbatim, so an edited-but-not-reindexed file produced
authoritative-looking rows pointing at the wrong lines, with nothing to
distinguish them from current ones.
"""

from __future__ import annotations

import pytest

from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_references import search_references


@pytest.fixture
def indexed_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "core.py").write_text("def greet(name):\n    return name\n")
    (src / "caller.py").write_text(
        "from core import greet\n\n\ndef run():\n    return greet('x')\n"
    )
    (src / "other.py").write_text(
        "from core import greet\n\n\ndef other():\n    return greet('y')\n"
    )
    index_folder(str(tmp_path), storage_dir=str(tmp_path / ".idx"))
    return tmp_path


def _run(repo):
    return search_references("greet", str(repo), storage_dir=str(repo / ".idx"), limit=50)


def _rows_for(result, needle):
    return [r for r in result["results"] if needle in r["file_path"]]


def test_fresh_results_are_not_marked_stale(indexed_repo):
    result = _run(indexed_repo)

    assert result["results"], "fixture should produce occurrences"
    assert all(r.get("is_stale") is False for r in result["results"])
    assert result["stale_file_count"] == 0


def test_modified_file_marks_only_its_own_rows_stale(indexed_repo):
    caller = indexed_repo / "src" / "caller.py"
    caller.write_text("# shifted\n" * 5 + caller.read_text())

    result = _run(indexed_repo)

    stale_rows = _rows_for(result, "caller.py")
    assert stale_rows, "caller.py should still return occurrences"
    assert all(r["is_stale"] is True for r in stale_rows), (
        "rows from an edited, un-reindexed file must be marked stale"
    )

    untouched = _rows_for(result, "other.py")
    assert untouched and all(r["is_stale"] is False for r in untouched), (
        "staleness must be per-file, not global"
    )

    assert result["stale_file_count"] == 1


def test_reindex_clears_staleness(indexed_repo):
    caller = indexed_repo / "src" / "caller.py"
    caller.write_text("# shifted\n" * 5 + caller.read_text())
    assert _run(indexed_repo)["stale_file_count"] == 1

    index_folder(str(indexed_repo), storage_dir=str(indexed_repo / ".idx"))

    result = _run(indexed_repo)
    assert result["stale_file_count"] == 0
    assert all(r["is_stale"] is False for r in result["results"])


def test_deleted_file_is_stale_not_an_error(indexed_repo):
    (indexed_repo / "src" / "caller.py").unlink()

    result = _run(indexed_repo)

    assert "error" not in result, "a deleted backing file must not fail the lookup"
    stale_rows = _rows_for(result, "caller.py")
    assert stale_rows and all(r["is_stale"] is True for r in stale_rows)
    assert result["stale_file_count"] == 1


def test_lookup_does_not_reindex_as_a_side_effect(indexed_repo):
    """Freshness is reported, never repaired."""
    caller = indexed_repo / "src" / "caller.py"
    caller.write_text("# shifted\n" * 5 + caller.read_text())

    _run(indexed_repo)

    assert _run(indexed_repo)["stale_file_count"] == 1, (
        "lookup must not silently refresh the index"
    )
