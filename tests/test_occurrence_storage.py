"""Lossless occurrence persistence, migration, and malformed-body recovery."""

from __future__ import annotations

import json
import weakref
from copy import deepcopy
from itertools import product
from unittest.mock import patch

import pytest

from lgrep.parser.symbols import make_occurrence_id
from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools._index_freshness import load_fresh_index
from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_references import search_references


@pytest.fixture(autouse=True)
def isolated_cache():
    IndexStore._cache.clear()
    yield
    IndexStore._cache.clear()


def occurrence(name="helper", *, path="src/module.py", start=17, test=False, enclosing=None):
    return {
        "id": make_occurrence_id(path, name, start),
        "name": name,
        "file_path": path,
        "start_byte": start,
        "end_byte": start + len(name.encode()),
        "line_number": 3,
        "line_text": f"    {name}({name}) # café",
        "kind": "call",
        "enclosing_symbol_id": enclosing,
        "is_test_file": test,
    }


def write_body(store, repo, body):
    store._dir.mkdir(parents=True, exist_ok=True)
    target = store._index_path(str(repo))
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    IndexStore._cache.clear()
    return target


def packed_body(repo):
    return {
        "repo_path": str(repo),
        "files": {},
        "symbols": {},
        "occurrences": {"helper": [[0, 17, 23, 3, 1, 2, None, False]]},
        "version": "2.3",
        "storage_version": 1,
        "occurrence_strings": ["src/module.py", "    helper(helper) # café", "call"],
    }


def test_canonical_rows_use_shared_strings_and_recover_in_writer_order(tmp_path):
    repo = tmp_path / "repo"
    store = IndexStore(tmp_path / "store")
    rows = [occurrence(start=17), occurrence(start=24)]
    index = CodeIndex(str(repo), {}, {}, {"helper": rows}, version="2.3")
    original = deepcopy(index.occurrences)
    store.save(index)
    body = json.loads(store._index_path(str(repo)).read_text())
    assert body["storage_version"] == 1
    assert body["occurrences"]["helper"] == [
        [0, 17, 23, 3, 1, 2, None, False],
        [0, 24, 30, 3, 1, 2, None, False],
    ]
    assert body["occurrence_strings"] == [rows[0]["file_path"], rows[0]["line_text"], "call"]
    assert index.occurrences == original, "packing must not mutate the caller's rows"
    IndexStore._cache.clear()
    loaded = store.load(str(repo))
    assert loaded is not None and isinstance(loaded.occurrences, dict)
    assert loaded.occurrences == original
    assert [list(row) for row in loaded.occurrences["helper"]] == [list(row) for row in rows]


def test_decode_releases_packed_rows_as_they_are_replaced(tmp_path):
    from lgrep.storage._occurrences import decode_occurrences

    class PackedRow(list):
        pass

    body = packed_body(tmp_path / "repo")
    first = PackedRow(body["occurrences"]["helper"][0])
    retained = weakref.ref(first)
    body["occurrences"]["helper"] = [first, [0, 24, 30, 3, 1, 2, None, False]]
    del first

    def check_release(path, name, start):
        if start == 24:
            assert retained() is None, "do not retain all packed rows beside all decoded rows"
        return make_occurrence_id(path, name, start)

    with patch("lgrep.storage._occurrences.make_occurrence_id", check_release):
        decoded = decode_occurrences(body)
    assert [row["start_byte"] for row in decoded["helper"]] == [17, 24]


def test_round_trip_across_unicode_flags_enclosing_and_kinds(tmp_path):
    store = IndexStore(tmp_path / "store")
    rows = {}
    for n, (name, path, test, enclosing, kind) in enumerate(
        product(
            ("helper", "λ"),
            ("module.py", "src/über.py"),
            (False, True),
            (None, "src/über.py:function:outer"),
            ("call", "reference", "import", "attribute"),
        )
    ):
        row = occurrence(name, path=path, start=n * 7, test=test, enclosing=enclosing)
        row["kind"] = kind
        rows.setdefault(name, []).append(row)
    index = CodeIndex(
        str(tmp_path / "repo"), {}, {"symbol": {"start_line": 1, "end_line": 8}}, rows
    )
    store.save(index)
    assert json.loads(store._index_path(index.repo_path).read_text())["storage_version"] == 1
    IndexStore._cache.clear()
    loaded = store.load(index.repo_path)
    assert loaded is not None and loaded == index
    for name in rows:
        assert [list(row) for row in loaded.occurrences[name]] == [list(row) for row in rows[name]]


@pytest.mark.parametrize("variant", ["id", "missing", "extra", "order", "flag", "position", "name"])
def test_noncanonical_rows_remain_inline_and_lossless(tmp_path, variant):
    row = occurrence()
    if variant == "id":
        row["id"] = "custom-id"
    elif variant == "missing":
        del row["line_text"]
    elif variant == "extra":
        row["future_field"] = {"nested": [1, None]}
    elif variant == "order":
        row = dict(reversed(list(row.items())))
    elif variant == "flag":
        row["is_test_file"] = 1
    elif variant == "position":
        row["start_byte"] = True
    else:
        row["name"] = "different"
    store = IndexStore(tmp_path / "store")
    index = CodeIndex(str(tmp_path / "repo"), {}, {}, {"helper": [occurrence(), row]})
    store.save(index)
    body = json.loads(store._index_path(index.repo_path).read_text())
    assert body["storage_version"] == 1
    assert isinstance(body["occurrences"]["helper"][0], list)
    assert body["occurrences"]["helper"][1] == row
    IndexStore._cache.clear()
    loaded = store.load(index.repo_path)
    assert loaded is not None and loaded.occurrences == index.occurrences
    assert list(loaded.occurrences["helper"][1]) == list(row)


@pytest.mark.parametrize("repo", ["github:owner/repo@main", "/local/empty"])
def test_empty_occurrences_and_remote_indexes_round_trip(tmp_path, repo):
    store = IndexStore(tmp_path)
    index = CodeIndex(repo, {"module.py": "hash"}, {"symbol": {"start_line": 1, "end_line": 2}})
    store.save(index)
    assert json.loads(store._index_path(repo).read_text())["storage_version"] == 1
    IndexStore._cache.clear()
    assert store.load(repo) == index


def test_legacy_is_read_only_until_explicit_noop_transcode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def helper():\n    return helper()\n")
    store = IndexStore(tmp_path / "store")
    index_folder(str(repo), storage_dir=store._dir)
    index = store.load(str(repo))
    assert index is not None
    target = write_body(
        store,
        repo,
        {
            "repo_path": str(repo),
            "files": index.files,
            "symbols": index.symbols,
            "occurrences": index.occurrences,
            "version": "2.3",
            "walk_truncated": False,
        },
    )
    legacy = target.read_bytes()
    with patch(
        "lgrep.tools.index_folder._extractor.extract_full", side_effect=AssertionError("reparse")
    ):
        checked, refreshed = load_fresh_index(str(repo), storage_dir=store._dir)
        assert checked is not None and not refreshed
        assert target.read_bytes() == legacy
        result = index_folder(str(repo), storage_dir=store._dir)
    assert result["files_indexed"] == 0 and result["files_skipped"] == 1
    assert json.loads(target.read_text())["storage_version"] == 1
    IndexStore._cache.clear()
    loaded = store.load(str(repo))
    assert loaded is not None and loaded == index
    assert loaded.occurrences == index.occurrences
    before = target.read_bytes()
    index_folder(str(repo), storage_dir=store._dir)
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "problem",
    [
        "future",
        "version_type",
        "version_bool",
        "missing_table",
        "table_type",
        "table_value",
        "buckets_type",
        "bucket_type",
        "row_type",
        "row_length",
        "negative_index",
        "large_index",
        "bool_index",
        "string_index",
        "null_index",
        "bad_enclosing",
        "start_type",
        "end_type",
        "line_type",
        "flag_type",
        "legacy_arrays",
        "legacy_bucket",
        "non_utf8",
    ],
)
def test_bad_encoding_is_logged_as_unloadable(tmp_path, problem):
    store = IndexStore(tmp_path / "store")
    repo = tmp_path / "repo"
    body = packed_body(repo)
    row = body["occurrences"]["helper"][0]
    if problem == "future":
        body["storage_version"] = 2
    elif problem == "version_type":
        body["storage_version"] = "1"
    elif problem == "version_bool":
        body["storage_version"] = True
    elif problem == "missing_table":
        del body["occurrence_strings"]
    elif problem == "table_type":
        body["occurrence_strings"] = {}
    elif problem == "table_value":
        body["occurrence_strings"][0] = None
    elif problem == "buckets_type":
        body["occurrences"] = []
    elif problem == "bucket_type":
        body["occurrences"]["helper"] = {}
    elif problem == "row_type":
        body["occurrences"]["helper"][0] = "row"
    elif problem == "row_length":
        row.pop()
    elif problem in {"negative_index", "large_index", "bool_index", "string_index", "null_index"}:
        row[0] = {
            "negative_index": -1,
            "large_index": 3,
            "bool_index": True,
            "string_index": "0",
            "null_index": None,
        }[problem]
    elif problem == "bad_enclosing":
        row[6] = -1
    elif problem == "start_type":
        row[1] = "17"
    elif problem == "end_type":
        row[2] = 23.0
    elif problem == "line_type":
        row[3] = False
    elif problem == "flag_type":
        row[7] = 0
    elif problem.startswith("legacy"):
        del body["storage_version"]
        del body["occurrence_strings"]
        if problem == "legacy_bucket":
            body["occurrences"]["helper"] = {}
    target = write_body(store, repo, body)
    if problem == "non_utf8":
        target.write_bytes(b"\xff")
    with patch("lgrep.storage.index_store.log") as log:
        assert store.load(str(repo)) is None
        log.warning.assert_called_once()
        assert log.warning.call_args.args == ("index_load_failed",)


@pytest.mark.parametrize("body", [[], 42])
def test_non_object_body_is_logged_as_unloadable(tmp_path, body):
    store = IndexStore(tmp_path / "store")
    repo = tmp_path / "repo"
    write_body(store, repo, body)
    with patch("lgrep.storage.index_store.log") as log:
        assert store.load(str(repo)) is None
        log.warning.assert_called_once_with(
            "index_load_failed", repo=str(repo.resolve()), error="Invalid symbol index body"
        )


def test_failed_publication_keeps_previous_body_and_storage_metadata(tmp_path):
    store = IndexStore(tmp_path)
    repo = str(tmp_path / "repo")
    original = CodeIndex(repo, {}, {}, {"helper": [occurrence()]})
    store.save(original)
    target = store._index_path(repo)
    before = target.read_bytes()
    replacement = CodeIndex(repo, {}, {}, {"helper": [occurrence(start=80)]})
    with (
        patch("lgrep.storage.index_store.os.replace", side_effect=OSError("full")),
        pytest.raises(OSError, match="full"),
    ):
        store.save(replacement)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    assert store.load(repo) is original
    assert replacement._storage_version == 0


def test_seed_sidecar_counts_and_reference_key_order_survive_disk_decode(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    text = "def helper():\n    return helper()\n"
    (source / "module.py").write_text(text)
    (target / "module.py").write_text(text)
    store = IndexStore(tmp_path / "store")
    index_folder(str(source), storage_dir=store._dir)
    original = store.load(str(source))
    assert original is not None
    assert json.loads(store._index_path(str(source)).read_text())["storage_version"] == 1
    assert store.seed_from(str(source), str(target))
    IndexStore._cache.clear()
    seeded = store.load(str(target))
    assert seeded is not None and seeded.occurrences == original.occurrences
    index_folder(str(target), storage_dir=store._dir)
    IndexStore._cache.clear()
    result = search_references("helper", str(target), storage_dir=store._dir)
    assert result["total_matches"] > 0
    assert list(result["results"][0]) == [*list(original.occurrences["helper"][0]), "is_stale"]
    store._meta_path(str(target)).unlink()
    assert str(target) in store.list_repos()
    meta = json.loads(store._meta_path(str(target)).read_text())
    assert meta["occurrences"] == sum(map(len, original.occurrences.values()))
