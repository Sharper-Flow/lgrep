"""Bound query work when an index is larger than the global retention budget."""

from __future__ import annotations

import os
import subprocess

import pytest

from lgrep.discovery import FileDiscovery
from lgrep.parser.extractor import SymbolExtractor
from lgrep.storage.index_store import IndexStore
from lgrep.tools.get_symbol import get_symbols
from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_symbols import search_symbols


@pytest.fixture(params=[False, True], ids=["checkout", "linked-checkout"])
def large_index(tmp_path, monkeypatch, request):
    root = tmp_path / "checkout"
    root.mkdir()
    for number in range(1005):
        (root / f"m{number:04d}.ts").write_text(f"function fn_{number}() {{ return 1; }}\n")
    if request.param:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        for argv in (
            ["init"],
            ["add", "."],
            ["commit", "-m", "fixture"],
            ["worktree", "add", "-b", "linked", str(tmp_path / "linked")],
        ):
            subprocess.run(["git", *argv], cwd=root, env=env, capture_output=True, check=True)
        root = tmp_path / "linked"

    discover = FileDiscovery.find_files
    monkeypatch.setattr(FileDiscovery, "find_files", lambda self: iter(sorted(discover(self))))
    store_dir = tmp_path / "store"
    assert index_folder(str(root), storage_dir=store_dir, max_files=1100)["files_indexed"] == 1005
    # Exercise the measured >64-MiB retention case without a large CI fixture.
    IndexStore._cache.clear()
    monkeypatch.setattr(IndexStore, "CACHE_LIMIT_BYTES", 1)
    return root, store_dir


@pytest.mark.parametrize("change", ["unchanged", "mtime_only", "edited_last_file"])
def test_search_loads_one_snapshot_and_publishes_only_changes(large_index, monkeypatch, change):
    root, store_dir = large_index
    target = root / "m1004.ts"
    query = "fn_1004"
    if change == "edited_last_file":
        query = "far_target"
        target.write_text("// café 雪\nfunction far_target() { return 42; }\n", encoding="utf-8")
    if change != "unchanged":
        # Filesystem mtimes can be coarser than a test step; order the change explicitly.
        stamp = IndexStore(store_dir).last_indexed_at(str(root)) + 0.001
        os.utime(target, (stamp, stamp))
        # Advance the check clock beyond its two-second conservative margin.
        monkeypatch.setattr(
            "lgrep.tools.index_folder.time.time_ns", lambda: int((stamp + 3) * 1_000_000_000)
        )

    calls = {"load": 0, "save": 0, "parse": 0}

    def counted(name, original):
        def wrapper(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(IndexStore, "load", counted("load", IndexStore.load))
    monkeypatch.setattr(IndexStore, "save", counted("save", IndexStore.save))
    monkeypatch.setattr(
        SymbolExtractor, "extract_full", counted("parse", SymbolExtractor.extract_full)
    )
    result = search_symbols(query, str(root), storage_dir=store_dir)
    assert result["total_matches"] == 1
    assert result["index_refreshed"] is (change != "unchanged")
    expected_loads = 1 if change == "unchanged" else 2
    expected_work = int(change == "edited_last_file")
    assert calls == {"load": expected_loads, "save": expected_work, "parse": expected_work}

    if change == "mtime_only":
        calls.update(load=0, save=0, parse=0)
        again = search_symbols(query, str(root), storage_dir=store_dir)
        assert again["index_refreshed"] is False, (
            "a no-op refresh must settle without rewriting JSON"
        )
        assert calls == {"load": 1, "save": 0, "parse": 0}
    elif change == "edited_last_file":
        symbol = get_symbols([result["results"][0]["id"]], str(root), storage_dir=store_dir)
        assert symbol["symbols"][0]["source"] == "function far_target() { return 42; }"


def test_concurrent_publication_cannot_hide_change_from_loaded_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "source.ts"
    target.write_text("function before() {}\n")
    store_dir = tmp_path / "store"
    index_folder(str(root), storage_dir=store_dir)
    store = IndexStore(store_dir)
    index_path = store._index_path(str(root.resolve()))
    load = IndexStore.load
    published = False

    def load_then_publish(self, repo_path):
        nonlocal published
        index = load(self, repo_path)
        if not published:
            published = True
            indexed_at = store.last_indexed_at(str(root))
            target.write_text("function after() {}\n")
            os.utime(target, (indexed_at + 1, indexed_at + 1))
            index_folder(str(root), storage_dir=store_dir)
            # Order old index < source edit < publication despite coarse mtimes.
            os.utime(index_path, (indexed_at + 2, indexed_at + 2))
        return index

    monkeypatch.setattr(IndexStore, "load", load_then_publish)
    result = search_symbols("after", str(root), storage_dir=store_dir)
    assert result["total_matches"] == 1


def test_noop_check_keeps_body_and_cached_snapshot(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "source.ts"
    target.write_text("function unchanged() {}\n")
    store_dir = tmp_path / "store"
    index_folder(str(root), storage_dir=store_dir)
    store = IndexStore(store_dir)
    index = store.load(str(root))
    path = store._index_path(str(root))
    body = path.read_bytes()
    os.utime(target, None)
    index_folder(str(root), storage_dir=store_dir)
    assert path.read_bytes() == body
    assert store.load(str(root)) is index


def test_noop_check_does_not_hide_edit_during_walk(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "source.ts"
    target.write_text("function before() {}\n")
    store_dir = tmp_path / "store"
    index_folder(str(root), storage_dir=store_dir)
    # Model an mtime rounded below the wall clock despite an edit after read.
    walk_start_ns = 2_000_000_000_000_000_000
    edit_mtime_ns = walk_start_ns - 1_000_000_000
    monkeypatch.setattr("lgrep.tools.index_folder.time.time_ns", lambda: walk_start_ns)
    read = type(target).read_bytes
    edited = False

    def read_then_edit(self):
        nonlocal edited
        content = read(self)
        if self == target and not edited:
            edited = True
            target.write_text("function after() {}\n")
            os.utime(target, ns=(edit_mtime_ns, edit_mtime_ns))
        return content

    monkeypatch.setattr(type(target), "read_bytes", read_then_edit)
    result = index_folder(str(root), storage_dir=store_dir)
    assert result["files_indexed"] == 0
    assert search_symbols("after", str(root), storage_dir=store_dir)["total_matches"] == 1


def test_missing_index_during_noop_check_serves_loaded_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "source.ts"
    target.write_text("function retained() {}\n")
    store_dir = tmp_path / "store"
    index_folder(str(root), storage_dir=store_dir)
    store = IndexStore(store_dir)
    index_path = store._index_path(str(root))
    stamp = store.last_indexed_at(str(root)) + 1
    os.utime(target, (stamp, stamp))
    mark_checked = IndexStore.mark_checked

    def remove_then_check(self, index, checked_at_ns):
        index_path.unlink()
        return mark_checked(self, index, checked_at_ns)

    monkeypatch.setattr(IndexStore, "mark_checked", remove_then_check)
    result = search_symbols("retained", str(root), storage_dir=store_dir)
    assert result["total_matches"] == 1
    assert result["results"][0]["name"] == "retained"
    assert not index_path.exists(), "a failed check must not recreate the pruned index"
