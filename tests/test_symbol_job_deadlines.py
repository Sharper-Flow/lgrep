"""Budget stops publish the same safe partial windows as file caps."""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from structlog.testing import capture_logs

from lgrep.exceptions import OperationCancelled
from lgrep.server import LgrepContext, tools_symbols
from lgrep.storage.index_store import CodeIndex, IndexStore
from tests.test_explicit_symbol_jobs import context, settled

folder = importlib.import_module("lgrep.tools.index_folder")
remote = importlib.import_module("lgrep.tools.index_repo")


@pytest.fixture(autouse=True)
def extractor_fixture_isolation():
    """Restored test hooks must not hide later class-level parser instrumentation."""
    assert "extract_full" not in vars(folder._extractor)
    yield
    assert "extract_full" not in vars(folder._extractor)


def test_folder_deadline_after_lock_keeps_existing_entries(tmp_path, monkeypatch):
    root, store = tmp_path / "repo", tmp_path / "symbols"
    root.mkdir()
    (root / "a.py").write_text("def first(): pass\n")
    (root / "b.py").write_text("def second(): pass\n")
    folder.index_folder(str(root), storage_dir=store)
    baseline = IndexStore(store).load(str(root))
    clock = [0.0]
    monkeypatch.setattr(
        folder, "time", SimpleNamespace(monotonic=lambda: clock[0], time_ns=time.time_ns)
    )
    original = IndexStore.repo_lock

    @contextmanager
    def waited(self, key):
        with original(self, key):
            clock[0] = 2.0
            yield

    monkeypatch.setattr(IndexStore, "repo_lock", waited)
    result = folder.index_folder(str(root), storage_dir=store, deadline=1.0)
    saved = IndexStore(store).load(str(root))
    assert result["files_indexed"] == 0
    assert result["walk_truncated"] and result["truncation_reason"] == "deadline"
    assert saved.files == baseline.files and saved.symbols == baseline.symbols
    assert saved.occurrences == baseline.occurrences and saved.walk_truncated


def test_folder_deadline_between_files_preserves_out_of_window(tmp_path, monkeypatch):
    from lgrep.discovery import FileDiscovery

    root, store = tmp_path / "repo", tmp_path / "symbols"
    root.mkdir()
    paths = [root / "a.py", root / "b.py"]
    for path in paths:
        path.write_text(f"def {path.stem}(): pass\n")
    folder.index_folder(str(root), storage_dir=store)
    baseline = IndexStore(store).load(str(root))
    paths[0].write_text("def changed(): pass\n")
    clock = [0.0]
    monkeypatch.setattr(FileDiscovery, "find_files", lambda self: iter(paths))
    monkeypatch.setattr(
        folder, "time", SimpleNamespace(monotonic=lambda: clock[0], time_ns=time.time_ns)
    )
    extract = type(folder._extractor).extract_full

    def slow(*args, **kwargs):
        value = extract(*args, **kwargs)
        clock[0] = 2.0
        return value

    monkeypatch.setattr(type(folder._extractor), "extract_full", slow)
    result = folder.index_folder(str(root), storage_dir=store, deadline=1.0)
    saved = IndexStore(store).load(str(root))
    assert result["files_indexed"] == 1 and result["truncation_reason"] == "deadline"
    assert saved.files["b.py"] == baseline.files["b.py"]
    assert "b.py:function:b" in saved.symbols


def test_folder_shutdown_before_save_does_not_publish(tmp_path, monkeypatch):
    root, store = tmp_path / "repo", tmp_path / "symbols"
    root.mkdir()
    (root / "a.py").write_text("def first(): pass\n")
    stop = threading.Event()
    extract = type(folder._extractor).extract_full
    saved = []

    def stopping(*args, **kwargs):
        result = extract(*args, **kwargs)
        stop.set()
        return result

    monkeypatch.setattr(type(folder._extractor), "extract_full", stopping)
    monkeypatch.setattr(IndexStore, "save", lambda *_: saved.append(True))
    with pytest.raises(OperationCancelled):
        folder.index_folder(str(root), storage_dir=store, cancel_event=stop)
    assert not saved


@pytest.mark.parametrize("capped", [False, True])
def test_folder_deadline_after_finished_work_does_not_replace_stop_reason(
    tmp_path, monkeypatch, capped
):
    from lgrep.discovery import FileDiscovery

    root, store = tmp_path / "repo", tmp_path / "symbols"
    root.mkdir()
    paths = [root / "a.py", root / "b.py"] if capped else [root / "a.py"]
    for path in paths:
        path.write_text(f"def {path.stem}(): pass\n")
    clock = [0.0]
    monkeypatch.setattr(
        folder, "time", SimpleNamespace(monotonic=lambda: clock[0], time_ns=time.time_ns)
    )
    monkeypatch.setattr(FileDiscovery, "find_files", lambda self: iter(paths))
    extract = type(folder._extractor).extract_full
    read_bytes = type(root).read_bytes

    def parse(*args, **kwargs):
        result = extract(*args, **kwargs)
        if not capped:
            clock[0] = 2.0
        return result

    def read(self):
        result = read_bytes(self)
        if self == paths[-1] and capped:
            clock[0] = 2.0
        return result

    monkeypatch.setattr(type(folder._extractor), "extract_full", parse)
    monkeypatch.setattr(type(root), "read_bytes", read)
    result = folder.index_folder(str(root), storage_dir=store, max_files=1, deadline=1.0)
    assert result["files_indexed"] == 1
    assert result["walk_truncated"] is capped
    assert result["truncation_reason"] == ("max_files" if capped else None)
    assert IndexStore(store).load(str(root)).walk_truncated is capped


@pytest.mark.asyncio
@pytest.mark.parametrize("capped", [False, True])
@pytest.mark.parametrize("partial_baseline", [False, True])
async def test_remote_deadline_after_last_parse_does_not_replace_stop_reason(
    tmp_path, monkeypatch, capped, partial_baseline
):
    import tree_sitter_language_pack

    if partial_baseline:
        IndexStore(tmp_path / "symbols").save(
            CodeIndex(
                repo_path="github:owner/repo@HEAD",
                files={"old.py": "old-hash"},
                symbols={},
                walk_truncated=True,
            )
        )
    clock = [0.0]
    monkeypatch.setattr(remote, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real_client = httpx.AsyncClient

    def response(request):
        if "git/trees" in request.url.path:
            paths = ["a.py", "b.py"] if capped else ["a.py"]
            return httpx.Response(
                200,
                json={
                    "tree": [{"type": "blob", "path": path} for path in paths],
                    "truncated": False,
                },
            )
        return httpx.Response(200, content=b"def first(): pass\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    parser = tree_sitter_language_pack.get_parser("python")

    def parse(content):
        result = parser.parse(content)
        clock[0] = 2.0
        return result

    monkeypatch.setattr(
        tree_sitter_language_pack, "get_parser", lambda _: SimpleNamespace(parse=parse)
    )
    result = await remote.index_repo(
        "owner/repo", storage_dir=tmp_path / "symbols", max_files=1, deadline=1.0
    )
    assert result["files_indexed"] == 1
    assert result["truncated"] is capped
    assert result["truncation_reason"] == ("max_files" if capped else None)
    assert result["published"] is True


@pytest.mark.asyncio
async def test_remote_last_wave_deadline_timeouts_still_report_deadline(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(remote, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real_client = httpx.AsyncClient

    def response(request):
        if "git/trees" in request.url.path:
            return httpx.Response(
                200, json={"tree": [{"type": "blob", "path": "a.py"}], "truncated": False}
            )
        clock[0] = 2.0
        raise httpx.ReadTimeout("remaining job budget expired", request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    result = await remote.index_repo("owner/repo", storage_dir=tmp_path / "symbols", deadline=1.0)
    assert result["files_indexed"] == 0
    assert result["truncated"] and result["truncation_reason"] == "deadline"


@pytest.mark.asyncio
@pytest.mark.parametrize("expire", ["wave", "parse"])
async def test_remote_shared_deadline_stops_fetch_or_parse(tmp_path, monkeypatch, expire):
    import tree_sitter_language_pack

    clock = [0.0]
    requests, parses = [], []
    monkeypatch.setattr(remote, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real_client = httpx.AsyncClient

    def response(request):
        requests.append(request.url.path)
        if "git/trees" in request.url.path:
            if expire == "wave":
                clock[0] = 2.0
            return httpx.Response(
                200,
                json={
                    "tree": [{"type": "blob", "path": "a.py"}, {"type": "blob", "path": "b.py"}],
                    "truncated": False,
                },
            )
        return httpx.Response(200, content=b"def first(): pass\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    parser = tree_sitter_language_pack.get_parser("python")

    def parse(content):
        parses.append(True)
        clock[0] = 2.0
        return parser.parse(content)

    monkeypatch.setattr(
        tree_sitter_language_pack, "get_parser", lambda _: SimpleNamespace(parse=parse)
    )
    result = await remote.index_repo("owner/repo", storage_dir=tmp_path / "symbols", deadline=1.0)
    assert result["truncated"] and result["truncation_reason"] == "deadline"
    assert result["files_indexed"] == (0 if expire == "wave" else 1)
    assert len(requests) == (1 if expire == "wave" else 3)
    assert len(parses) == (0 if expire == "wave" else 1)


@pytest.mark.asyncio
async def test_remote_job_does_not_stop_at_old_tool_timeout(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(remote, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv("LGREP_TOOL_TIMEOUT_S", "25")
    real_client = httpx.AsyncClient

    def response(request):
        if "git/trees" in request.url.path:
            clock[0] = 21.0
            return httpx.Response(
                200, json={"tree": [{"type": "blob", "path": "a.py"}], "truncated": False}
            )
        return httpx.Response(200, content=b"def first(): pass\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    result = await remote.index_repo("owner/repo", storage_dir=tmp_path / "symbols", deadline=150.0)
    assert result["files_indexed"] == 1 and not result["truncated"]


@pytest.mark.asyncio
async def test_remote_deadline_before_fetch_makes_no_request(tmp_path, monkeypatch):
    requests = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(
            **kw, transport=httpx.MockTransport(lambda request: requests.append(request))
        ),
    )
    result = await remote.index_repo("owner/repo", storage_dir=tmp_path / "symbols", deadline=0)
    assert not requests
    assert result["files_indexed"] == 0
    assert result["truncated"] and result["truncation_reason"] == "deadline"


@pytest.mark.asyncio
@pytest.mark.parametrize("during_save", [False, True])
async def test_remote_shutdown_before_or_during_save(tmp_path, monkeypatch, during_save):
    import tree_sitter_language_pack

    stop = threading.Event()
    saved = []
    real_client = httpx.AsyncClient

    def response(request):
        return (
            httpx.Response(
                200, json={"tree": [{"type": "blob", "path": "a.py"}], "truncated": False}
            )
            if "git/trees" in request.url.path
            else httpx.Response(200, content=b"def first(): pass\n")
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    parser = tree_sitter_language_pack.get_parser("python")

    def parse(content):
        if not during_save:
            stop.set()
        return parser.parse(content)

    def save(self, index):
        saved.append(True)
        stop.set()

    monkeypatch.setattr(
        tree_sitter_language_pack, "get_parser", lambda _: SimpleNamespace(parse=parse)
    )
    monkeypatch.setattr(IndexStore, "save", save)
    if during_save:
        result = await remote.index_repo(
            "owner/repo", storage_dir=tmp_path / "symbols", cancel_event=stop
        )
        assert result["files_indexed"] == 1 and saved == [True]
    else:
        with pytest.raises(OperationCancelled):
            await remote.index_repo(
                "owner/repo", storage_dir=tmp_path / "symbols", cancel_event=stop
            )
        assert not saved


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("tree_error", [False, True])
async def test_remote_fetch_errors_redact_explicit_and_resolved_credentials(
    tmp_path, monkeypatch, explicit, tree_error
):
    import json

    token = "fetch-error-credential-sentinel"
    monkeypatch.setenv("LGREP_GITHUB_TOKEN", token)
    real_client = httpx.AsyncClient

    def response(request):
        if "git/trees" in request.url.path and not tree_error:
            return httpx.Response(
                200, json={"tree": [{"type": "blob", "path": "a.py"}], "truncated": False}
            )
        raise httpx.ConnectError("x" * 490 + token, request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    with capture_logs() as logs:
        result = await remote.index_repo(
            "owner/repo", storage_dir=tmp_path / "symbols", github_token=token if explicit else None
        )
    assert token not in json.dumps([result, logs])
    if tree_error:
        assert "error" in result and "[redacted]" in result["error"]
    else:
        event = next(event for event in logs if event["event"] == "github_file_fetch_failed")
        assert len(event["error"]) <= 500 and token[:10] not in event["error"]


@pytest.mark.asyncio
async def test_remote_held_fetch_keeps_mcp_loop_available(tmp_path, monkeypatch):
    from lgrep.storage import index_store

    monkeypatch.setattr(index_store, "DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
    entered, release = threading.Event(), threading.Event()
    threads = []
    real_client = httpx.AsyncClient
    app = LgrepContext(transport="stdio")

    def response(request):
        threads.append(threading.current_thread().name)
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"tree": [], "truncated": False})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(response)),
    )
    try:
        submitted = await asyncio.wait_for(
            tools_symbols.index_symbols_repo("owner/repo", ctx=context(app)), 1
        )
        assert await asyncio.to_thread(entered.wait, 1)
        status = await asyncio.wait_for(
            tools_symbols.get_symbol_index_job(submitted["job_id"], ctx=context(app)), 1
        )
        assert status["state"] == "running"
        assert threads and threads[0].startswith("lgrep-explicit")
        release.set()
        assert (await settled(app.runtime, submitted["job_id"]))["state"] == "completed"
        await tools_symbols.mcp._tool_manager.get_tool("get_symbol_index_job").run(
            {"job_id": submitted["job_id"]}, context=context(app), convert_result=True
        )
    finally:
        release.set()
        app.runtime.shutdown()
