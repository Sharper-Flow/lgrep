"""Incomplete GitHub observations must not replace a saved symbol index."""

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from lgrep.exceptions import OperationCancelled
from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools.index_repo import index_repo
from lgrep.tools.search_symbols import search_symbols

KEY = "github:owner/repo@main"


@pytest.fixture
def remote(tmp_path, monkeypatch):
    state = SimpleNamespace(
        tree={"tree": [{"type": "blob", "path": "new.py"}], "truncated": False},
        fetch_failure=False,
        storage=tmp_path / "symbols",
    )
    client = httpx.AsyncClient

    class DecodeFailure(httpx.Response):
        def json(self, **kwargs):
            raise state.tree

    def response(request):
        if "git/trees" in request.url.path:
            if isinstance(state.tree, Exception):
                return DecodeFailure(200)
            return httpx.Response(200, content=json.dumps(state.tree).encode())
        if state.fetch_failure:
            raise httpx.ReadError("file unavailable", request=request)
        return httpx.Response(200, content=b"def refreshed(): pass\n")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(response))
    )
    return state


def seed(storage):
    store = IndexStore(storage_dir=storage)
    store.save(
        CodeIndex(
            repo_path=KEY,
            files={"old.py": "last-good-hash"},
            symbols={
                "old.py:function:previous": {
                    "id": "old.py:function:previous",
                    "name": "previous",
                    "kind": "function",
                    "file_path": "old.py",
                }
            },
        )
    )
    return store


def saved_bytes(storage):
    return {path.name: path.read_bytes() for path in storage.glob("index_*.json")}


def make_incomplete(remote, monkeypatch, reason):
    if reason == "tree_truncated":
        remote.tree["truncated"] = True
    elif reason == "fetch_failed":
        remote.fetch_failure = True
    elif reason == "parse_failed":

        def fail(_):
            raise RuntimeError("parser unavailable")

        monkeypatch.setattr("tree_sitter_language_pack.get_parser", fail)
    elif reason == "max_files":
        remote.tree["tree"].append({"type": "blob", "path": "other.py"})
        return {"max_files": 1}
    elif reason == "deadline":
        return {"deadline": 0}
    return {}


@pytest.mark.parametrize(
    "reason", ["tree_truncated", "fetch_failed", "parse_failed", "max_files", "deadline"]
)
async def test_incomplete_refresh_keeps_saved_bytes_and_search_results(remote, monkeypatch, reason):
    seed(remote.storage)
    before = saved_bytes(remote.storage)
    kwargs = make_incomplete(remote, monkeypatch, reason)

    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, **kwargs)

    assert saved_bytes(remote.storage) == before
    assert result["truncated"] is True
    assert result["truncation_reason"] == reason
    assert result["published"] is False
    assert search_symbols("previous", KEY, storage_dir=remote.storage)["total_matches"] == 1
    assert search_symbols("refreshed", KEY, storage_dir=remote.storage)["total_matches"] == 0


@pytest.mark.parametrize(
    "reason", ["tree_truncated", "fetch_failed", "parse_failed", "max_files", "deadline"]
)
async def test_first_partial_build_is_published_with_persisted_coverage(
    remote, monkeypatch, reason
):
    kwargs = make_incomplete(remote, monkeypatch, reason)

    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, **kwargs)

    assert result["truncated"] is True
    assert result["truncation_reason"] == reason
    assert result["published"] is True
    store = IndexStore(storage_dir=remote.storage)
    assert store.load(KEY).walk_truncated is True
    body = json.loads(store._index_path(KEY).read_text())
    assert body["walk_truncated"] is True
    if reason in ("fetch_failed", "parse_failed"):
        assert body["files"] == {}
        assert body["symbols"] == {}


@pytest.mark.parametrize(
    "tree",
    [
        None,
        [],
        {},
        pytest.param({"tree": []}, id="missing-truncation"),
        {"tree": None, "truncated": False},
        {"tree": [None], "truncated": False},
        {"tree": [{"type": "blob"}], "truncated": False},
        {"tree": [{"type": "blob", "path": 123}], "truncated": False},
        {"tree": [{"type": "unknown", "path": "new.py"}], "truncated": False},
        {"tree": [{"type": "blob", "path": ""}], "truncated": False},
        {"tree": [{"type": "blob", "path": "new.py"}] * 2, "truncated": False},
        {"tree": [], "truncated": "false"},
        ValueError("invalid JSON"),
        RecursionError("tree nested too deeply"),
    ],
)
@pytest.mark.parametrize("existing", [False, True])
async def test_invalid_tree_is_a_reported_error_without_publication(remote, tree, existing):
    if existing:
        seed(remote.storage)
    before = saved_bytes(remote.storage)
    remote.tree = tree

    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)

    assert "Invalid GitHub tree response" in result["error"]
    assert saved_bytes(remote.storage) == before


@pytest.mark.parametrize("empty", [False, True])
async def test_complete_refresh_replaces_old_index_including_complete_empty_tree(remote, empty):
    store = seed(remote.storage)
    if empty:
        remote.tree["tree"] = []

    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)

    assert result["published"] is True
    assert result["truncated"] is False
    current = store.load(KEY)
    assert current.walk_truncated is False
    assert "old.py" not in current.files
    assert len(current.files) == (0 if empty else 1)


async def test_publication_decision_and_save_hold_the_same_repo_lock(remote, monkeypatch):
    original_lock, original_save = IndexStore.repo_lock, IndexStore.save
    held = []

    @contextmanager
    def lock(self, key):
        with original_lock(self, key):
            held.append(key)
            try:
                yield
            finally:
                held.pop()

    def save(self, index):
        assert held == [KEY]
        original_save(self, index)

    monkeypatch.setattr(IndexStore, "repo_lock", lock)
    monkeypatch.setattr(IndexStore, "save", save)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert result["published"] is True


async def test_incomplete_attempt_preserves_a_baseline_published_while_fetching(
    remote, monkeypatch
):
    remote.tree["truncated"] = True
    original_lock = IndexStore.repo_lock
    before = {}

    @contextmanager
    def lock(self, key):
        # Model a writer finishing before this attempt acquires the lock.
        seed(remote.storage)
        before.update(saved_bytes(remote.storage))
        with original_lock(self, key):
            yield

    monkeypatch.setattr(IndexStore, "repo_lock", lock)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert result["published"] is False
    assert saved_bytes(remote.storage) == before


async def test_incomplete_attempt_preserves_even_a_partial_first_build(remote):
    remote.tree["truncated"] = True
    first = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert first["published"] is True
    before = saved_bytes(remote.storage)
    remote.tree["tree"] = [{"type": "blob", "path": "another.py"}]
    second = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert second["published"] is False
    assert saved_bytes(remote.storage) == before


async def test_tree_truncation_is_not_hidden_by_the_file_cap(remote):
    remote.tree["truncated"] = True
    remote.tree["tree"].append({"type": "blob", "path": "another.py"})
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, max_files=1)
    assert result["truncated"] is True
    assert result["truncation_reason"] == "tree_truncated"


@pytest.mark.parametrize("stage", ["before_parse", "between_parses"])
async def test_deadline_preserves_baseline_after_successful_fetch(remote, monkeypatch, stage):
    import importlib

    import lgrep.parser.extractor as extractor

    module = importlib.import_module("lgrep.tools.index_repo")
    seed(remote.storage)
    before = saved_bytes(remote.storage)
    remote.tree["tree"].append({"type": "blob", "path": "other.py"})
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_extract = extractor._extract_symbols_from_tree

    def extract(*args):
        symbols = original_extract(*args)
        clock[0] = 2.0
        return symbols

    async def run_sync(fn):
        if stage == "before_parse":
            clock[0] = 2.0
        return fn()

    monkeypatch.setattr(extractor, "_extract_symbols_from_tree", extract)
    result = await index_repo(
        "owner/repo", ref="main", storage_dir=remote.storage, deadline=1.0, run_sync=run_sync
    )
    assert result["files_indexed"] == (0 if stage == "before_parse" else 1)
    assert result["truncation_reason"] == "deadline"
    assert result["published"] is False
    assert saved_bytes(remote.storage) == before


async def test_shutdown_while_waiting_for_publication_lock_prevents_save(remote, monkeypatch):
    seed(remote.storage)
    before = saved_bytes(remote.storage)
    stop = threading.Event()
    original_lock = IndexStore.repo_lock

    @contextmanager
    def lock(self, key):
        with original_lock(self, key):
            stop.set()
            yield

    monkeypatch.setattr(IndexStore, "repo_lock", lock)
    with pytest.raises(OperationCancelled):
        await index_repo("owner/repo", ref="main", storage_dir=remote.storage, cancel_event=stop)
    assert saved_bytes(remote.storage) == before


async def test_extraction_failure_does_not_publish_half_a_file(remote, monkeypatch):
    import lgrep.parser.extractor as extractor

    original_extract = extractor._extract_symbols_from_tree

    def extract(*args):
        yield from original_extract(*args)
        raise RuntimeError("extraction failed after yielding a symbol")

    monkeypatch.setattr(extractor, "_extract_symbols_from_tree", extract)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    current = IndexStore(remote.storage).load(KEY)
    assert result["truncation_reason"] == "parse_failed"
    assert result["files_indexed"] == result["symbols_indexed"] == 0
    assert result["published"] is True and current.walk_truncated is True
    assert current.files == current.symbols == {}


@pytest.mark.parametrize("after_replace", [False, True])
async def test_save_failure_is_not_reported_as_success_or_rolled_back(
    remote, monkeypatch, after_replace
):
    import lgrep.storage.index_store as index_store

    store = seed(remote.storage)
    before = saved_bytes(remote.storage)

    def fail(*args):
        raise OSError("injected publication failure")

    if after_replace:
        monkeypatch.setattr(index_store, "_write_sidecar", fail)
    else:
        monkeypatch.setattr(index_store.os, "replace", fail)

    with pytest.raises(OSError, match="injected publication failure"):
        await index_repo("owner/repo", ref="main", storage_dir=remote.storage)

    if after_replace:
        current = store.load(KEY)
        assert set(current.files) == {"new.py"}
        assert current.walk_truncated is False
    else:
        assert saved_bytes(remote.storage) == before
    assert not list(remote.storage.glob("*.tmp"))
