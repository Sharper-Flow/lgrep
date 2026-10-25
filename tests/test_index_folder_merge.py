"""The incremental symbol merge: exact results, and per-file cost that does not grow with the index."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

import lgrep.tools.index_folder as folder
from lgrep.parser.symbols import Occurrence, Symbol, make_occurrence_id
from lgrep.storage.index_store import IndexStore

if TYPE_CHECKING:
    from pathlib import Path

_NAMES = ["err", "t", "self", "load", "save", "x"]
_is_test_file = folder._is_test_file_heuristic


@pytest.fixture(autouse=True)
def isolated_cache():
    IndexStore._cache.clear()
    yield
    IndexStore._cache.clear()


def _reference_merge(existing, parsed, deleted, incremental):
    """The merge specification: filter every symbol and bucket by file for each change."""
    symbols_dict = dict(existing.symbols) if existing else {}
    occurrences_dict = dict(existing.occurrences) if existing else {}
    for rel_path, symbols, occurrences in parsed:
        if incremental:
            symbols_dict = {
                sid: sdata
                for sid, sdata in symbols_dict.items()
                if sdata.get("file_path") != rel_path
            }
            occurrences_dict = {
                name: [occ for occ in occs if occ.get("file_path") != rel_path]
                for name, occs in occurrences_dict.items()
            }
            occurrences_dict = {name: occs for name, occs in occurrences_dict.items() if occs}
        for sym in symbols:
            symbol_id = sym.id
            if symbol_id in symbols_dict:
                symbol_id = f"{sym.id}@{sym.start_byte}"
            symbols_dict[symbol_id] = {
                "id": symbol_id,
                "name": sym.name,
                "kind": sym.kind,
                "file_path": sym.file_path,
                "start_byte": sym.start_byte,
                "end_byte": sym.end_byte,
                "docstring": sym.docstring,
                "decorators": sym.decorators,
                "parent": sym.parent,
            }
        for occ in occurrences:
            occurrences_dict.setdefault(occ.name, []).append(
                {
                    "id": occ.id,
                    "name": occ.name,
                    "file_path": occ.file_path,
                    "start_byte": occ.start_byte,
                    "end_byte": occ.end_byte,
                    "line_number": occ.line_number,
                    "line_text": occ.line_text,
                    "kind": occ.kind,
                    "enclosing_symbol_id": occ.enclosing_symbol_id,
                    "is_test_file": _is_test_file(rel_path),
                }
            )
    if deleted:
        symbols_dict = {
            sid: sdata
            for sid, sdata in symbols_dict.items()
            if sdata.get("file_path") not in deleted
        }
        occurrences_dict = {
            name: [occ for occ in occs if occ.get("file_path") not in deleted]
            for name, occs in occurrences_dict.items()
        }
        occurrences_dict = {name: occs for name, occs in occurrences_dict.items() if occs}
    return symbols_dict, occurrences_dict


def _fake_extract(file_path: Path, repo_root: Path | None = None):
    """Deterministic symbols and occurrences derived from the file's content.

    Some symbol ids omit the path, so ids collide across files and exercise the
    @start_byte suffix and cross-file overwrites. Most occurrence names come
    from a small pool, so those buckets hold rows from many files.
    """
    rel = str(file_path.relative_to(repo_root))
    rng = random.Random(f"{rel}:{file_path.read_bytes()!r}")
    symbols = []
    for _ in range(rng.randint(0, 4)):
        name = rng.choice(["f", "g", "h"])
        symbol_id = f"shared:{name}" if rng.random() < 0.25 else f"{rel}:function:{name}"
        start = rng.randint(0, 6)
        symbols.append(Symbol(symbol_id, name, "function", rel, start, start + 9))
    occurrences = []
    for position in range(rng.randint(0, 7)):
        # Rare names leave buckets that empty out and come back elsewhere.
        name = rng.choice(_NAMES) if rng.random() < 0.6 else f"rare{rng.randint(0, 9)}"
        start = position * 10
        occurrences.append(
            Occurrence(
                make_occurrence_id(rel, name, start),
                name,
                rel,
                start,
                start + len(name),
                position + 1,
                f"line {position} {name}",
                "reference",
            )
        )
    return symbols, occurrences


def _write(root: Path, rel: str, rng: random.Random) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {rng.random()}\n")


def _run_and_compare(monkeypatch, root, storage, **kwargs):
    """Index once and assert the published index equals the reference merge."""
    store = IndexStore(storage_dir=storage)
    key = str(root.resolve())
    incremental = kwargs.get("incremental", True)
    existing = store.load(key) if incremental else None
    snapshot = deepcopy(existing)
    parsed = []

    def recording_extract(_self, file_path, repo_root=None):
        symbols, occurrences = _fake_extract(file_path, repo_root)
        parsed.append((str(file_path.relative_to(repo_root)), symbols, occurrences))
        return symbols, occurrences

    monkeypatch.setattr(type(folder._extractor), "extract_full", recording_extract)
    result, index = folder._index_folder_with_snapshot(str(root), storage, **kwargs)
    assert "error" not in result

    deleted = set(existing.files) - set(index.files) if existing else set()
    expected_symbols, expected_occurrences = _reference_merge(
        deepcopy(snapshot), parsed, deleted, incremental
    )
    assert list(index.symbols.items()) == list(expected_symbols.items())
    assert list(index.occurrences.items()) == list(expected_occurrences.items())
    assert result["files_deleted"] == len(deleted)
    # The loaded index may be the cached object other queries hold.
    if existing is not None:
        assert existing.symbols == snapshot.symbols
        assert list(existing.occurrences.items()) == list(snapshot.occurrences.items())
    return store, key, result


def _reshape_saved_index(store, key, rng, *, old_version):
    """Interleave bucket rows, add an empty bucket, and optionally force a full refresh."""
    index = store.load(key)
    occurrences = {}
    for name, rows in index.occurrences.items():
        rows = list(rows)
        rng.shuffle(rows)
        occurrences[name] = rows
    occurrences["never_used"] = []
    store.save(
        replace(index, occurrences=occurrences, version="2.2" if old_version else index.version)
    )
    IndexStore._cache.clear()


@pytest.mark.parametrize("seed", range(12))
def test_merge_matches_reference_across_refresh_histories(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)
    root = tmp_path / "repo"
    storage = tmp_path / "symbols"
    files = [f"pkg{n % 3}/mod_{n}.py" for n in range(14)] + ["tests/test_a.py"]
    for rel in files:
        _write(root, rel, rng)

    _run_and_compare(monkeypatch, root, storage)  # cold build

    for step in range(7):
        live = sorted(str(p.relative_to(root)) for p in root.rglob("*.py"))
        for rel in rng.sample(live, k=min(len(live), rng.randint(0, 4))):
            _write(root, rel, rng)
        for rel in rng.sample(live, k=min(len(live), rng.randint(0, 2))):
            (root / rel).unlink()
        for n in range(rng.randint(0, 2)):
            _write(root, f"new/s{step}_{n}.py", rng)
        if rng.random() < 0.4:
            store = IndexStore(storage_dir=storage)
            _reshape_saved_index(store, str(root.resolve()), rng, old_version=rng.random() < 0.5)
        max_files = rng.choice([500, 500, 2])  # 2 truncates the walk
        _, _, result = _run_and_compare(monkeypatch, root, storage, max_files=max_files)
        if max_files == 2 and result["walk_truncated"]:
            assert result["truncation_reason"] == "max_files"

    _run_and_compare(monkeypatch, root, storage, incremental=False)


def test_test_file_flag_is_computed_once_per_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    body = "".join(f"value_{n} = helper(value_{n})\n" for n in range(40))
    (root / "tests" / "test_many.py").write_text(f"def helper(x):\n    return x\n{body}")
    (root / "app.py").write_text(f"def helper(x):\n    return x\n{body}")
    calls = []

    def counting(rel_path):
        calls.append(rel_path)
        return _is_test_file(rel_path)

    monkeypatch.setattr(folder, "_is_test_file_heuristic", counting)
    _, index = folder._index_folder_with_snapshot(str(root), tmp_path / "symbols")

    assert sorted(calls) == ["app.py", "tests/test_many.py"]
    flags = {
        row["file_path"]: row["is_test_file"] for rows in index.occurrences.values() for row in rows
    }
    assert flags == {"app.py": False, "tests/test_many.py": True}


class _CountingDict(dict):
    reads = 0

    def get(self, *args):
        _CountingDict.reads += 1
        return super().get(*args)


def _large_merge(files: int) -> folder._IndexMerge:
    symbols = {}
    occurrences = {name: [] for name in _NAMES}
    for n in range(files):
        path = f"src/m{n}.py"
        for name in ("f", "g"):
            sid = f"{path}:function:{name}"
            symbols[sid] = _CountingDict(id=sid, file_path=path)
        for name in _NAMES:
            occurrences[name].append(_CountingDict(name=name, file_path=path))
    return folder._IndexMerge(symbols, occurrences)


@pytest.mark.parametrize("files", [200, 4000])
def test_removing_a_file_reads_only_that_files_entries(files):
    merge = _large_merge(files)
    # The first removal indexes the merge and filters each bucket; the second
    # splits each bucket into runs. Each happens once per bucket.
    merge.remove_file("src/m0.py")
    merge.remove_file("src/m1.py")

    _CountingDict.reads = 0
    for n in range(2, 52):
        merge.remove_file(f"src/m{n}.py")
        merge.add_symbol(f"src/m{n}.py:function:f", {"file_path": f"src/m{n}.py"})
        for name in _NAMES:
            merge.add_occurrence({"name": name, "file_path": f"src/m{n}.py"})

    # Bounded by the 50 files' own entries (two symbols, one row per bucket),
    # whatever the index size.
    assert _CountingDict.reads <= 50 * (2 + len(_NAMES))
    occurrences = merge.occurrences()
    assert [row["file_path"] for row in occurrences["err"][-50:]] == [
        f"src/m{n}.py" for n in range(2, 52)
    ]
    assert len(occurrences["err"]) == files - 2
