"""Remote publication preserves complete baselines and rejects failed refreshes."""

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


def seed(storage, *, partial=False):
    store = IndexStore(storage_dir=storage)
    store.save(
        CodeIndex(
            repo_path=KEY,
            walk_truncated=partial,
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
@pytest.mark.parametrize("partial", [False, True])
async def test_complete_refresh_replaces_old_index_including_complete_empty_tree(
    remote, empty, partial
):
    store = seed(remote.storage, partial=partial)
    if empty:
        remote.tree["tree"] = []

    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)

    assert result["published"] is True
    assert result["truncated"] is False
    current = store.load(KEY)
    assert current.walk_truncated is False
    assert "old.py" not in current.files
    assert len(current.files) == (0 if empty else 1)


@pytest.mark.parametrize("partial", [False, True])
async def test_publication_decision_and_save_hold_the_same_repo_lock(remote, monkeypatch, partial):
    if partial:
        seed(remote.storage, partial=True)
        remote.tree["truncated"] = True
    original_lock, original_save = IndexStore.repo_lock, IndexStore.save
    original_coverage = IndexStore.has_incomplete_index
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

    def coverage(self, key):
        assert held == [KEY]
        return original_coverage(self, key)

    monkeypatch.setattr(IndexStore, "repo_lock", lock)
    monkeypatch.setattr(IndexStore, "save", save)
    monkeypatch.setattr(IndexStore, "has_incomplete_index", coverage)
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


@pytest.mark.parametrize("limit", ["tree_truncated", "max_files"])
async def test_limit_only_refresh_replaces_partial_baseline_without_merging(
    remote, monkeypatch, limit
):
    store = seed(remote.storage, partial=True)
    kwargs = make_incomplete(remote, monkeypatch, limit)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, **kwargs)
    assert result["published"] is True
    assert result["truncated"] is True
    assert result["truncation_reason"] == limit
    current = store.load(KEY)
    assert current.walk_truncated is True
    assert set(current.files) == {"new.py"}
    assert search_symbols("previous", KEY, storage_dir=remote.storage)["total_matches"] == 0
    assert search_symbols("refreshed", KEY, storage_dir=remote.storage)["total_matches"] == 1


@pytest.mark.parametrize("caps", [(1, 2), (2, 1)])
async def test_changed_file_cap_can_widen_or_reduce_partial_coverage(remote, caps):
    remote.tree["tree"] = [{"type": "blob", "path": f"{name}.py"} for name in "abc"]
    store = IndexStore(remote.storage)
    for cap in caps:
        result = await index_repo(
            "owner/repo", ref="main", storage_dir=remote.storage, max_files=cap
        )
        assert result["published"] is True
        assert result["truncation_reason"] == "max_files"
        assert len(store.load(KEY).files) == cap


@pytest.mark.parametrize("limit", ["tree_truncated", "max_files"])
@pytest.mark.parametrize("failure", ["fetch_failed", "parse_failed"])
async def test_a_limit_reason_cannot_hide_a_failed_partial_refresh(
    remote, monkeypatch, limit, failure
):
    seed(remote.storage, partial=True)
    before = saved_bytes(remote.storage)
    kwargs = make_incomplete(remote, monkeypatch, limit)
    make_incomplete(remote, monkeypatch, failure)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, **kwargs)
    assert result["published"] is False
    assert result["truncation_reason"] == limit
    assert saved_bytes(remote.storage) == before


@pytest.mark.parametrize("marker", [None, False, "true", "false", 1, {}, []])
async def test_unknown_or_complete_coverage_does_not_allow_partial_replacement(remote, marker):
    store = seed(remote.storage)
    path = store._index_path(KEY)
    body = json.loads(path.read_text())
    if marker is None:
        del body["walk_truncated"]
    else:
        body["walk_truncated"] = marker
    path.write_text(json.dumps(body))
    before = saved_bytes(remote.storage)
    remote.tree["truncated"] = True
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert result["published"] is False
    assert saved_bytes(remote.storage) == before


@pytest.mark.parametrize(
    "body",
    [
        "not JSON",
        "[]",
        '{"walk_truncated":true}',
        '{"repo_path":"other","walk_truncated":true,"files":{},"symbols":{}}',
    ],
)
async def test_unknown_baseline_is_not_overwritten_by_a_limited_refresh(remote, body):
    store = seed(remote.storage)
    store._index_path(KEY).write_text(body)
    before = saved_bytes(remote.storage)
    remote.tree["truncated"] = True
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert result["published"] is False
    assert saved_bytes(remote.storage) == before


@pytest.mark.parametrize("error", [PermissionError, RecursionError])
async def test_unreadable_partial_baseline_is_not_assumed_missing(remote, monkeypatch, error):
    from pathlib import Path

    store = seed(remote.storage, partial=True)
    path = store._index_path(KEY)
    original = Path.read_text
    before = saved_bytes(remote.storage)
    remote.tree["truncated"] = True

    def read(self, *args, **kwargs):
        if self == path:
            raise error("injected read failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage)
    assert result["published"] is False
    assert saved_bytes(remote.storage) == before


async def test_tree_truncation_is_not_hidden_by_the_file_cap(remote):
    remote.tree["truncated"] = True
    remote.tree["tree"].append({"type": "blob", "path": "another.py"})
    result = await index_repo("owner/repo", ref="main", storage_dir=remote.storage, max_files=1)
    assert result["truncated"] is True
    assert result["truncation_reason"] == "tree_truncated"


@pytest.mark.parametrize("stage", ["before_tree", "tree_timeout", "before_fetch"])
async def test_deadline_before_fetching_preserves_partial_baseline(tmp_path, monkeypatch, stage):
    import importlib

    module = importlib.import_module("lgrep.tools.index_repo")
    storage = tmp_path / "symbols"
    seed(storage, partial=True)
    before = saved_bytes(storage)
    clock = [2.0 if stage == "before_tree" else 0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = httpx.AsyncClient

    def response(request):
        clock[0] = 2.0
        if stage == "tree_timeout":
            raise httpx.ReadTimeout("job budget expired", request=request)
        tree = {"tree": [{"type": "blob", "path": "new.py"}], "truncated": True}
        return httpx.Response(200, content=json.dumps(tree).encode())

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(response))
    )
    result = await index_repo("owner/repo", ref="main", storage_dir=storage, deadline=1.0)
    assert result["truncation_reason"] == "deadline"
    assert result["published"] is False
    assert saved_bytes(storage) == before


@pytest.mark.parametrize("stage", ["before_parse", "between_parses"])
@pytest.mark.parametrize("partial", [False, True])
async def test_deadline_preserves_baseline_after_successful_fetch(
    remote, monkeypatch, stage, partial
):
    import importlib

    import lgrep.parser.extractor as extractor

    module = importlib.import_module("lgrep.tools.index_repo")
    seed(remote.storage, partial=partial)
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


async def test_shutdown_during_baseline_coverage_read_prevents_save(remote, monkeypatch):
    seed(remote.storage, partial=True)
    before = saved_bytes(remote.storage)
    remote.tree["truncated"] = True
    stop = threading.Event()
    original = IndexStore.has_incomplete_index

    def coverage(self, key):
        partial = original(self, key)
        stop.set()
        return partial

    monkeypatch.setattr(IndexStore, "has_incomplete_index", coverage)
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
