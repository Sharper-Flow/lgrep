"""Acceptance checks for symbol-cache retention and reload from durable bytes."""

from __future__ import annotations

import gc
import socket
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools.prune_symbols import prune_symbols

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def isolated_symbol_cache() -> Iterator[None]:
    IndexStore._cache.clear()
    try:
        yield
    finally:
        IndexStore._cache.clear()
        gc.collect()


@pytest.fixture
def small_cache(monkeypatch: pytest.MonkeyPatch) -> int:
    limit = 4096
    monkeypatch.setattr(IndexStore, "CACHE_LIMIT_BYTES", limit, raising=False)
    return limit


def _index(repo: Path, *, padding: int = 128) -> CodeIndex:
    return CodeIndex(
        repo_path=str(repo),
        files={"cached.py": "fixture-content-hash"},
        symbols={
            "cached.py:function:cached": {
                "name": "cached",
                "kind": "function",
                "file_path": "cached.py",
                "start_line": 1,
                "end_line": 1,
                "signature": "cached" + "x" * padding,
            }
        },
        occurrences={"cached": [{"file_path": "cached.py", "line": 1}]},
    )


def _info() -> dict[str, int]:
    snapshot = getattr(IndexStore, "cache_info", None)
    assert callable(snapshot), "IndexStore.cache_info() must expose cache accounting"
    info = snapshot()
    assert isinstance(info, dict)
    for key in ("entries", "tracked_bytes", "limit_bytes"):
        assert type(info[key]) is int, f"{key} must be an integer"
    assert info["entries"] >= 0
    assert info["limit_bytes"] > 0
    assert 0 <= info["tracked_bytes"] <= info["limit_bytes"]
    return info


def test_lru_room_is_released_before_decode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = IndexStore(tmp_path / "symbols")
    repos = [tmp_path / f"checkout-{name}" for name in "abc"]
    for repo in repos:
        store.save(_index(repo))
    sizes = [store._index_path(str(repo)).stat().st_size for repo in repos]
    assert sizes[0] == sizes[1] == sizes[2]
    IndexStore._cache.clear()
    monkeypatch.setattr(IndexStore, "CACHE_LIMIT_BYTES", sizes[0] * 2)
    first = store.load(str(repos[0]))
    second = store.load(str(repos[1]))
    first_ref, second_ref = weakref.ref(first), weakref.ref(second)
    del first, second
    target = store._index_path(str(repos[2]))
    read_text = Path.read_text
    checked = []

    def inspect_before_decode(path, *args, **kwargs):
        if path == target:
            assert first_ref() is None, "release LRU custody before constructing its replacement"
            assert second_ref() is not None, "keep entries that still fit beside the new body"
            checked.append(True)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", inspect_before_decode)
    loaded = store.load(str(repos[2]))
    assert loaded is not None and checked == [True]
    assert _info()["tracked_bytes"] == sizes[1] + sizes[2]


def test_oversized_miss_preserves_other_cached_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = IndexStore(tmp_path / "symbols")
    small, large = tmp_path / "small", tmp_path / "large"
    store.save(_index(small))
    store.save(_index(large, padding=10000))
    IndexStore._cache.clear()
    small_size = store._index_path(str(small)).stat().st_size
    monkeypatch.setattr(IndexStore, "CACHE_LIMIT_BYTES", small_size)
    retained = store.load(str(small))
    assert store.load(str(large)) is not None
    assert store.load(str(small)) is retained
    assert _info()["tracked_bytes"] == small_size


def test_prune_releases_cached_deleted_index(tmp_path: Path) -> None:
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = tmp_path / "deleted-checkout"
    index = _index(repo)
    store.save(index)
    index_file = store._index_path(str(repo))
    assert store.load(str(repo)) is index
    retained = weakref.ref(index)
    del index
    gc.collect()
    assert retained() is not None

    preview = prune_symbols(storage_dir=store._dir, grace_seconds=0)
    assert preview["dry_run"] is True
    assert index_file.exists()
    assert retained() is not None

    report = prune_symbols(dry_run=False, storage_dir=store._dir, grace_seconds=0)
    assert report["failures"] == []
    assert report["deleted_files"] >= 1
    assert not index_file.exists()
    gc.collect()
    assert retained() is None, "prune must release its cache reference without a later load"


def test_load_missing_file_releases_cached_index(tmp_path: Path) -> None:
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = tmp_path / "checkout"
    repo.mkdir()
    index = _index(repo)
    store.save(index)
    assert store.load(str(repo)) is index
    retained = weakref.ref(index)
    del index
    store._index_path(str(repo)).unlink()

    assert store.load(str(repo)) is None
    gc.collect()
    assert retained() is None, "a missing-file load must drop its stale cache reference"


def test_cache_entries_bounded_under_distinct_checkout_churn(
    tmp_path: Path, small_cache: int
) -> None:
    stores = [IndexStore(storage_dir=tmp_path / f"symbols-{n}") for n in range(2)]
    repos = [tmp_path / f"checkout-{n:02d}" for n in range(32)]
    references = []
    sizes = []
    for n, repo in enumerate(repos):
        repo.mkdir()
        store = stores[n % len(stores)]
        index = _index(repo)
        store.save(index)
        references.append(weakref.ref(index))
        size = store._index_path(str(repo)).stat().st_size
        assert size < small_cache
        sizes.append(size)
        del index
        info = _info()
        assert info["limit_bytes"] == small_cache
        assert store.cache_info() == info

    gc.collect()
    assert sum(sizes) > 3 * small_cache
    assert any(ref() is None for ref in references), "churn must release actual index objects"
    assert _info()["entries"] <= small_cache // min(sizes)

    for _ in range(2):
        for n, repo in enumerate(repos):
            store = stores[n % len(stores)]
            loaded = store.load(str(repo))
            assert loaded is not None
            assert loaded.files == {"cached.py": "fixture-content-hash"}
            _info()
            store.save(loaded)
            del loaded
            _info()

    for n, repo in enumerate(repos):
        stores[n % len(stores)].delete_index(str(repo))
    info = _info()
    assert info["entries"] == info["tracked_bytes"] == 0


def test_evicted_current_index_reloads_without_reindex(
    tmp_path: Path, small_cache: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = tmp_path / "checkout"
    repo.mkdir()
    original = _index(repo)
    store.save(original)
    index_file = store._index_path(str(repo))
    last_good = index_file.read_bytes()
    assert store.load(str(repo)) is original

    for n in range(24):
        peer = _index(tmp_path / f"other-checkout-{n:02d}")
        store.save(peer)
        assert store._index_path(peer.repo_path).stat().st_size < small_cache
        _info()
    del peer

    def reject_save(*args: object, **kwargs: object) -> None:
        pytest.fail("reload must not rebuild or republish an evicted index")

    def reject_network(*args: object, **kwargs: object) -> None:
        pytest.fail("reload must not request embeddings or perform network I/O")

    monkeypatch.setattr(IndexStore, "save", reject_save)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    reloaded = IndexStore(storage_dir=store._dir).load(str(repo))
    assert reloaded is not None
    assert reloaded is not original, "the unused original must have been evicted"
    assert reloaded == original
    assert index_file.read_bytes() == last_good
    assert store.load(str(repo)) is reloaded
    _info()


def test_concurrent_query_and_eviction(tmp_path: Path, small_cache: int) -> None:
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = tmp_path / "checkout"
    repo.mkdir()
    original = _index(repo)
    store.save(original)
    retained = weakref.ref(original)
    expected_symbols = original.symbols.copy()
    del original
    acquired = threading.Event()
    eviction_finished = threading.Event()

    def query() -> None:
        try:
            loaded = store.load(str(repo))
            assert loaded is not None
            acquired.set()
            assert eviction_finished.wait(10), "eviction worker did not finish"
            assert loaded.symbols == expected_symbols
            assert loaded.files == {"cached.py": "fixture-content-hash"}
        finally:
            acquired.set()

    def churn(worker: int) -> None:
        assert acquired.wait(10), "query did not acquire its index"
        peer_store = IndexStore(storage_dir=tmp_path / f"other-symbols-{worker}")
        for n in range(24):
            peer = _index(tmp_path / f"other-checkout-{worker}-{n:02d}")
            peer_store.save(peer)
            assert peer_store._index_path(peer.repo_path).stat().st_size < small_cache
            assert peer_store.load(peer.repo_path) is not None
            _info()

    with ThreadPoolExecutor(max_workers=3) as pool:
        reader = pool.submit(query)
        writers = [pool.submit(churn, worker) for worker in range(2)]
        try:
            for writer in writers:
                writer.result(timeout=20)
        finally:
            eviction_finished.set()
        reader.result(timeout=20)

    gc.collect()
    assert retained() is None, "only the in-flight caller may retain an evicted index"
    restored = store.load(str(repo))
    assert restored is not None
    assert restored.symbols == expected_symbols
    store.delete_index(str(repo))
    for worker in range(2):
        peer_store = IndexStore(storage_dir=tmp_path / f"other-symbols-{worker}")
        for n in range(24):
            peer_store.delete_index(str(tmp_path / f"other-checkout-{worker}-{n:02d}"))
    info = _info()
    assert info["entries"] == info["tracked_bytes"] == 0


def test_cache_accounting_reports_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _info()
    assert initial["entries"] == initial["tracked_bytes"] == 0
    monkeypatch.setattr(IndexStore, "CACHE_LIMIT_BYTES", 4096, raising=False)
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = tmp_path / "checkout"
    first = _index(repo, padding=8)
    store.save(first)
    index_file = store._index_path(str(repo))
    first_size = index_file.stat().st_size
    assert _info()["entries"] == 1
    assert _info()["tracked_bytes"] == first_size

    replacement = _index(repo, padding=768)
    store.save(replacement)
    replacement_size = index_file.stat().st_size
    assert first_size != replacement_size < 4096
    info = _info()
    assert info["entries"] == 1
    assert info["tracked_bytes"] == replacement_size
    assert store.load(str(repo)) is replacement
    assert _info() == info

    def reject_disk_access(*args: object, **kwargs: object) -> None:
        pytest.fail("cache accounting must not scan durable index files")

    with monkeypatch.context() as guarded:
        for method in ("stat", "read_bytes", "read_text"):
            guarded.setattr(Path, method, reject_disk_access)
        memory_only = _info()
    assert memory_only == info

    store.delete_index(str(repo))
    assert _info()["entries"] == _info()["tracked_bytes"] == 0

    oversized = _index(tmp_path / "oversized-checkout", padding=16384)
    store.save(oversized)
    assert store._index_path(oversized.repo_path).stat().st_size > 4096
    oversized_path = oversized.repo_path
    oversized_symbols = oversized.symbols.copy()
    retained = weakref.ref(oversized)
    del oversized
    gc.collect()
    assert retained() is None, "an oversized index must not exceed the hard retention budget"
    loaded = store.load(oversized_path)
    assert loaded is not None
    assert loaded.symbols == oversized_symbols
    assert _info()["entries"] == _info()["tracked_bytes"] == 0


def test_invalidation_during_load_does_not_restore_cache_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = IndexStore(storage_dir=tmp_path / "symbols")
    repo = str(tmp_path / "checkout")
    store.save(_index(Path(repo)))
    index_file = store._index_path(repo)
    IndexStore._cache.clear()
    read_text = Path.read_text

    def read_then_delete(path: Path, *args: object, **kwargs: object) -> str:
        body = read_text(path, *args, **kwargs)
        if path == index_file:
            store.delete_index(repo)
        return body

    monkeypatch.setattr(Path, "read_text", read_then_delete)
    loaded = store.load(repo)
    assert loaded is not None
    assert not index_file.exists()
    reference = weakref.ref(loaded)
    del loaded
    gc.collect()
    assert reference() is None, "an in-flight read must not undo cache invalidation"
    assert _info()["entries"] == _info()["tracked_bytes"] == 0
