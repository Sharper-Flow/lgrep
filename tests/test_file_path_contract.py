"""A ``path`` argument may name a file; each tool family resolves it to its own scope."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lgrep.cli import _cmd_index_semantic, _cmd_init_ignore, _cmd_search_symbols
from lgrep.server import LgrepContext, mcp
from lgrep.server.lifecycle import ProjectState, _shutdown
from lgrep.storage import SearchResult, SearchResults
from lgrep.tools.get_file_tree import get_file_tree
from lgrep.tools.get_repo_outline import get_repo_outline
from lgrep.tools.get_symbol import get_symbols
from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_references import search_references
from lgrep.tools.search_symbols import search_symbols
from lgrep.tools.search_text import search_text
from tests.test_explicit_symbol_jobs import context, settled

OUTSIDE_CHECKOUT = "Path is a file outside a git checkout; pass a repository root: {path}"


def tool(name):
    """Return an MCP tool's handler, called as the server calls it."""
    return next(
        candidate for candidate in mcp._tool_manager.list_tools() if candidate.name == name
    ).fn


def last_json_line(output):
    return json.loads(next(line for line in reversed(output.splitlines()) if line.startswith("{")))


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A fresh Git checkout holding a Python file one level below its root."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "nested").mkdir()
    source = root / "nested" / "example.py"
    source.write_text("def target():\n    return 'needle'\n")
    (root / "nested" / "other.py").write_text("needle = True\n")
    monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path / "symbols"))
    return SimpleNamespace(root=root.resolve(), source=source.resolve())


@pytest.fixture
def loose_file(tmp_path, monkeypatch):
    """A file that no Git working tree contains, wherever the temp directory lies."""
    # Git does not look above a ceiling directory, so an ancestor checkout cannot claim it.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path / "symbols"))
    source = tmp_path / "loose.py"
    source.write_text("def target(): pass\n")
    return source


@pytest.fixture
def indexed(checkout):
    """The checkout indexed through its nested file, and that file's symbol ID."""
    result = index_folder(str(checkout.source))
    assert result["repo_path"] == str(checkout.root)
    (symbol,) = search_symbols("target", str(checkout.source))["results"]
    return checkout, symbol["id"]


# ── search_text searches one named file ──────────────────────────────────────


def test_text_search_of_a_file_returns_only_that_files_matches(checkout):
    result = search_text("needle", str(checkout.source))

    assert [(row["file_path"], row["line_number"]) for row in result["results"]] == [
        ("example.py", 2)
    ]
    assert result["files_searched"] == 1


def test_text_search_of_a_file_without_a_match_still_counts_the_file(checkout):
    result = search_text("absent", str(checkout.source))

    assert result["results"] == []
    assert result["files_searched"] == 1


def test_text_search_of_a_file_is_one_unit_when_the_limit_truncates(checkout):
    checkout.source.write_text("needle\nneedle\nneedle\n")

    result = search_text("needle", str(checkout.source), max_results=2)

    assert [row["line_number"] for row in result["results"]] == [1, 2]
    assert result["files_searched"] == 1


@pytest.mark.parametrize("stop", ["cancelled", "deadline"])
def test_text_search_of_a_file_stopped_before_reading_counts_no_file(checkout, stop):
    cancel_event, deadline = threading.Event(), None
    if stop == "cancelled":
        cancel_event.set()
    else:
        deadline = time.monotonic() - 1

    result = search_text(
        "needle", str(checkout.source), cancel_event=cancel_event, deadline=deadline
    )

    assert result["files_searched"] == 0
    assert result["truncated"] is True and result["truncation_reason"] == stop


def test_text_search_of_a_file_keeps_the_search_size_cap(checkout):
    checkout.source.write_text("needle" + "x" * 1024 * 1024)

    result = search_text("needle", str(checkout.source))

    assert result["results"] == []
    assert result["files_searched"] == 0


# ── tree and outline cover the file's parent ─────────────────────────────────


def test_file_tree_of_a_file_lists_its_parent(checkout):
    result = get_file_tree(str(checkout.source))

    assert result["repo_path"] == str(checkout.source.parent)
    assert sorted(result["files"]) == ["example.py", "other.py"]


def test_repo_outline_of_a_file_covers_its_parent(checkout):
    result = get_repo_outline(str(checkout.source))

    assert result["repo_path"] == str(checkout.source.parent)
    assert [entry["file_path"] for entry in result["files"]] == [str(checkout.source)]


@pytest.mark.asyncio
async def test_get_file_tree_tool_reports_the_parent_it_used(checkout):
    result = await tool("get_file_tree")(path=str(checkout.source))

    assert result["repo_path"] == str(checkout.source.parent)


# ── index-backed tools resolve a file to its checkout root ───────────────────


def test_symbol_query_of_a_file_in_a_fresh_checkout_bootstraps_that_checkout(checkout):
    result = search_symbols("target", str(checkout.source))

    assert result["repo_path"] == str(checkout.root)
    assert [symbol["name"] for symbol in result["results"]] == ["target"]


@pytest.mark.parametrize(
    "lookup",
    [
        lambda path, symbol_id: search_symbols("target", path),
        lambda path, symbol_id: get_symbols([symbol_id], path),
        lambda path, symbol_id: search_references("target", path),
    ],
    ids=["search_symbols", "get_symbols", "search_references"],
)
def test_symbol_lookups_given_a_file_name_the_checkout(indexed, lookup):
    checkout, symbol_id = indexed

    result = lookup(str(checkout.source), symbol_id)

    assert result["repo_path"] == str(checkout.root)


@pytest.mark.asyncio
async def test_search_symbols_tool_in_the_daemon_resolves_a_file_to_its_checkout(checkout):
    app = LgrepContext()
    try:
        result = await tool("search_symbols")(
            query="target", path=str(checkout.source), ctx=context(app)
        )
    finally:
        await _shutdown(app)

    assert result["repo_path"] == str(checkout.root)
    assert [symbol["name"] for symbol in result["results"]] == ["target"]


@pytest.mark.asyncio
async def test_index_symbols_folder_job_for_a_file_indexes_and_names_the_checkout(checkout):
    app = LgrepContext(transport="stdio")
    try:
        submitted = await tool("index_symbols_folder")(path=str(checkout.source), ctx=context(app))
        final = await settled(app.runtime, submitted["job_id"])
    finally:
        app.runtime.shutdown()

    assert final["state"] == "completed", final
    assert final["result"]["repo_path"] == str(checkout.root)
    assert final["result"]["files_indexed"] == 2


def _checkout_loaded_in(app, root):
    """Load ``root`` into ``app`` the way an earlier semantic search leaves it."""
    app.embedder = MagicMock()
    app.embedder.embed_query_async = AsyncMock(return_value=[0.1] * 1024)
    db = MagicMock()
    db.line_repair_done.return_value = True
    db.search_hybrid.return_value = SearchResults(
        results=[SearchResult("nested/example.py", 1, 2, "def target():", 0.9, "hybrid")],
        query_time_ms=1.0,
    )
    app.projects[str(root)] = ProjectState(db=db, indexer=MagicMock())


@pytest.mark.asyncio
async def test_search_semantic_tool_given_a_file_searches_its_loaded_checkout(checkout):
    app = LgrepContext()
    _checkout_loaded_in(app, checkout.root)

    result = await tool("search_semantic")(
        query="greet", path=str(checkout.source), ctx=context(app)
    )

    assert result["path"] == str(checkout.root)
    assert [hit["file_path"] for hit in result["results"]] == ["nested/example.py"]


@pytest.mark.asyncio
async def test_index_semantic_tool_given_a_file_indexes_and_names_its_checkout(
    checkout, monkeypatch
):
    import lgrep.server.tools_semantic as semantic

    state = ProjectState(db=MagicMock(), indexer=MagicMock())
    state.indexer.index_all.return_value = SimpleNamespace(
        file_count=2, chunk_count=3, duration_ms=1.0, total_tokens=10
    )
    monkeypatch.setattr(semantic, "_ensure_project_initialized", AsyncMock(return_value=state))

    result = await tool("index_semantic")(path=str(checkout.source), ctx=context(LgrepContext()))

    assert result["path"] == str(checkout.root)
    assert (result["file_count"], result["chunk_count"]) == (2, 3)


# ── a file outside any checkout is refused ───────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda path: search_symbols("target", path),
        lambda path: get_symbols(["loose.py:function:target"], path),
        lambda path: search_references("target", path),
        lambda path: index_folder(path),
    ],
    ids=["search_symbols", "get_symbols", "search_references", "index_folder"],
)
def test_index_backed_tools_refuse_a_file_outside_any_checkout(loose_file, call):
    result = call(str(loose_file))

    assert result["error"] == OUTSIDE_CHECKOUT.format(path=loose_file)


@pytest.mark.asyncio
async def test_index_symbols_folder_job_for_a_file_outside_a_checkout_fails_with_the_refusal(
    loose_file,
):
    app = LgrepContext(transport="stdio")
    try:
        submitted = await tool("index_symbols_folder")(path=str(loose_file), ctx=context(app))
        final = await settled(app.runtime, submitted["job_id"])
    finally:
        app.runtime.shutdown()

    assert final["state"] == "failed"
    assert final["error"] == OUTSIDE_CHECKOUT.format(path=loose_file)


# ── missing and unusable paths are told apart ────────────────────────────────


def test_a_missing_path_and_an_unusable_path_get_distinct_errors(tmp_path):
    missing, pipe = tmp_path / "missing", tmp_path / "pipe"
    os.mkfifo(pipe)

    assert search_text("x", str(missing))["error"] == f"Path does not exist: {missing}"
    assert search_symbols("x", str(missing))["error"] == f"Path does not exist: {missing}"
    assert (
        search_text("x", str(pipe))["error"] == f"Path is not a directory or a regular file: {pipe}"
    )


# ── CLI commands ─────────────────────────────────────────────────────────────


def test_init_ignore_refuses_a_file_and_writes_nothing(tmp_path, capsys):
    nested = tmp_path / "nested"
    nested.mkdir()
    source = nested / "example.py"
    source.write_text("pass\n")

    assert _cmd_init_ignore([str(source), "--force"]) == 1

    output = capsys.readouterr().out
    assert f"Path is a file; pass a directory: {source}" in output
    assert "is not a directory" not in output
    assert not list(tmp_path.rglob(".lgrepignore"))


def test_init_ignore_keeps_a_distinct_message_for_a_missing_path(tmp_path, capsys):
    missing = tmp_path / "missing"

    assert _cmd_init_ignore([str(missing)]) == 1

    output = capsys.readouterr().out
    assert f"Path does not exist: {missing}" in output
    assert "is not a directory" not in output


@pytest.mark.usefixtures("cli_logging")
def test_index_semantic_cli_refuses_a_file(checkout, capsys):
    assert _cmd_index_semantic([str(checkout.source)]) == 1

    output = capsys.readouterr().out
    assert f"Path is a file; pass a directory: {checkout.source}" in output
    assert "is not a directory" not in output


@pytest.mark.usefixtures("cli_logging")
def test_search_symbols_cli_given_a_file_answers_from_its_checkout(checkout, capsys):
    assert _cmd_search_symbols(["target", str(checkout.source)]) == 0

    result = last_json_line(capsys.readouterr().out)
    assert result["repo_path"] == str(checkout.root)
    assert [symbol["name"] for symbol in result["results"]] == ["target"]
