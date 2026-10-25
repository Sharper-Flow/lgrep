"""Symbol work outside the daemon interpreter.

Symbol builds and queries are CPU-bound Python. Run in the daemon, they hold
the GIL for long stretches, and every other tool call waits behind them: one
measurement saw ``search_text`` go from 0.47 s to 17 s during a 17 s build.
The daemon therefore sends all symbol work to one persistent worker process.
The worker owns the symbol index cache, freshness checks and refreshes,
bootstrap and explicit builds, and every query that reads an index or parses
source. The daemon sends small requests and receives small picklable results.

``SymbolOperations`` is that work. ``SymbolWorkerProcess`` runs it in the
worker process. ``InProcessSymbolWorker`` runs the same operations in the
calling thread; a ``RuntimeSupervisor`` built without a worker uses it.

This module stays outside ``lgrep.server`` and loads no lgrep module that
logs. The spawned worker imports it first, so the worker points stdout at
stderr and configures logging before any such module loads. Stdout belongs
to the daemon's stdio MCP channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import itertools
import json
import logging
import multiprocessing
import os
import pickle
import signal
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from lgrep.exceptions import OperationCancelled

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

    from lgrep.storage.index_store import CodeIndex, SymbolCacheInfo

log = structlog.get_logger(__name__)

# Seconds a stopping worker gets to finish running calls before it is killed.
SHUTDOWN_GRACE_S = 3.0

# Worker threads. The daemon's symbol lane (8 threads), explicit lane (1), and
# build-lane prune calls bound in-flight calls below this, so a request never
# waits for a worker thread.
WORKER_THREADS = 16

# Plain operations: name -> (module, function). Modules load on first use.
_QUERIES = {
    "get_symbols": ("lgrep.tools.get_symbol", "get_symbols"),
    "search_references": ("lgrep.tools.search_references", "search_references"),
    "get_file_outline": ("lgrep.tools.get_file_outline", "get_file_outline"),
    "get_repo_outline": ("lgrep.tools.get_repo_outline", "get_repo_outline"),
    "list_repos": ("lgrep.tools.list_repos", "list_repos"),
    "invalidate_cache": ("lgrep.tools.invalidate_cache", "invalidate_cache"),
    "prune_symbols": ("lgrep.tools.prune_symbols", "prune_symbols"),
}


class SymbolWorkerLost(Exception):
    """The worker exited, or restarted and lost the call's state. Retry the call."""


class SymbolOperations:
    """The symbol work one interpreter owns.

    ``stop_event`` is set only at daemon shutdown; explicit builds stop at
    their next cooperative check when it is set. A build ``deadline`` is a
    ``time.monotonic()`` value from the daemon; that clock is system-wide on
    Linux and macOS, so it means the same instant here. ``acquire`` keeps each
    checked index snapshot until the daemon releases its token, so queries
    sharing one refresh read that snapshot without loading the index again.
    """

    def __init__(self, stop_event: Any) -> None:
        self._stop = stop_event
        self._lock = threading.Lock()
        self._snapshots: dict[str, tuple[CodeIndex | None, bool]] = {}

    def run(self, op: str, args: tuple, kwargs: dict) -> Any:
        """Run one named operation and return its picklable result."""
        if op in _QUERIES:
            module, name = _QUERIES[op]
            return getattr(importlib.import_module(module), name)(*args, **kwargs)
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            raise ValueError(f"unknown symbol operation: {op!r}")
        return handler(*args, **kwargs)

    def _op_acquire(self, repo_key: str) -> str:
        """Check, refresh, or bootstrap one index; return its snapshot token."""
        from lgrep.tools import search_symbols

        snapshot = search_symbols.load_symbol_index(repo_key)
        # Unique across workers: a token from a lost worker must never name a
        # replacement worker's snapshot of another repository.
        token = f"snapshot-{uuid.uuid4().hex}"
        with self._lock:
            self._snapshots[token] = snapshot
        return token

    def _op_release(self, token: str) -> None:
        with self._lock:
            self._snapshots.pop(token, None)

    def _op_search_symbols(self, token: str, query: str, repo_key: str, **kwargs: Any) -> dict:
        from lgrep.tools import search_symbols

        with self._lock:
            snapshot = self._snapshots.get(token)
        if snapshot is None:
            raise SymbolWorkerLost("The symbol worker restarted and lost the checked index.")
        return search_symbols.search_symbols(query, repo_key, index_snapshot=snapshot, **kwargs)

    def _op_index_folder(self, path: str, **kwargs: Any) -> dict:
        from lgrep.tools import index_folder

        return index_folder.index_folder(path, cancel_event=self._stop, **kwargs)

    def _op_index_repo(self, repo: str, **kwargs: Any) -> dict:
        from lgrep.tools import index_repo

        async def inline(fn):
            return fn()

        return asyncio.run(
            index_repo.index_repo(repo, run_sync=inline, cancel_event=self._stop, **kwargs)
        )


class InProcessSymbolWorker:
    """Run symbol operations in the calling thread, in this interpreter."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._operations = SymbolOperations(self._stop)

    def call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        """Run one operation and return its result."""
        return self._operations.run(op, args, kwargs)

    def release(self, token: str) -> None:
        """Drop one acquired snapshot."""
        self._operations.run("release", (token,), {})

    def cache_info(self) -> SymbolCacheInfo:
        from lgrep.storage.index_store import IndexStore

        return IndexStore.cache_info()

    def stop(self) -> None:
        """Ask explicit builds to stop at their next cooperative check."""
        self._stop.set()

    def shutdown(self) -> None:
        self.stop()


class SymbolWorkerProcess:
    """Run symbol operations in one persistent spawned worker process.

    The worker starts on the first call and stays up; it is never recycled.
    If it exits unexpectedly, its in-flight calls raise ``SymbolWorkerLost``
    and the next call starts a replacement. ``call`` blocks its thread on a
    future, so the waiting daemon thread holds no GIL.
    """

    def __init__(self, *, shutdown_grace_s: float = SHUTDOWN_GRACE_S) -> None:
        self._mp = multiprocessing.get_context("spawn")
        # Created before the first worker and passed to every worker at start:
        # multiprocessing shares synchronization primitives only that way.
        self._stop = self._mp.Event()
        self._grace_s = shutdown_grace_s
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._process: multiprocessing.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self._reader: threading.Thread | None = None
        self._pending: dict[int, Future] = {}
        self._cache_info: SymbolCacheInfo | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        """PID of the running worker, or None when none runs."""
        with self._lock:
            return self._process.pid if self._process is not None else None

    def call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        """Run one operation in the worker and wait for its result."""
        return self._submit(op, args, kwargs, start=True).result()

    def release(self, token: str) -> None:
        """Drop one acquired snapshot without waiting; a gone worker has none."""
        with contextlib.suppress(SymbolWorkerLost, OperationCancelled):
            self._submit("release", (token,), {}, start=False)

    def cache_info(self) -> SymbolCacheInfo:
        """Return the worker's symbol cache use as of its last reply."""
        with self._lock:
            if self._cache_info is not None:
                return self._cache_info
        from lgrep.storage.index_store import IndexStore

        return {"entries": 0, "tracked_bytes": 0, "limit_bytes": IndexStore.CACHE_LIMIT_BYTES}

    def stop(self) -> None:
        """Ask explicit builds to stop at their next cooperative check."""
        self._stop.set()

    def shutdown(self) -> None:
        """Stop the worker; kill it if running calls outlast the grace period."""
        with self._lock:
            self._closed = True
            self._stop.set()
            process, conn, reader = self._process, self._conn, self._reader
            if conn is not None:
                with contextlib.suppress(OSError, ValueError):
                    conn.send(("exit",))
        if process is None:
            return
        process.join(self._grace_s)
        if process.is_alive():
            log.warning("symbol_worker_killed", pid=process.pid, grace_s=self._grace_s)
            process.kill()
            process.join(1.0)
        if reader is not None:
            reader.join(1.0)

    def _submit(self, op: str, args: tuple, kwargs: dict, *, start: bool) -> Future:
        future: Future = Future()
        with self._lock:
            if self._closed:
                raise OperationCancelled("Symbol worker stopped during shutdown")
            if self._process is None:
                if not start:
                    future.set_result(None)
                    return future
                self._start_locked()
            request_id = next(self._ids)
            self._pending[request_id] = future
            try:
                self._conn.send(("call", request_id, op, args, kwargs))
            except (OSError, ValueError) as exc:
                self._pending.pop(request_id, None)
                raise SymbolWorkerLost("The symbol worker is not reachable.") from exc
        return future

    def _start_locked(self) -> None:
        from lgrep.storage import index_store

        parent_conn, child_conn = self._mp.Pipe()
        process = self._mp.Process(
            target=_worker_main,
            args=(child_conn, self._stop, str(index_store.DEFAULT_SYMBOLS_DIR)),
            name="lgrep-symbol-worker",
            daemon=True,
        )
        process.start()
        child_conn.close()
        self._process, self._conn = process, parent_conn
        self._reader = threading.Thread(
            target=self._read,
            args=(process, parent_conn),
            name="lgrep-symbol-reader",
            daemon=True,
        )
        self._reader.start()
        log.info("symbol_worker_started", pid=process.pid)

    def _read(self, process: multiprocessing.process.BaseProcess, conn: Connection) -> None:
        try:
            while True:
                message = conn.recv()
                if message[0] == "log":
                    _emit_worker_event(process.pid, message[1])
                    continue
                _, request_id, ok, value, cache_info = message
                with self._lock:
                    future = self._pending.pop(request_id, None)
                    self._cache_info = cache_info
                if future is None:
                    continue
                if ok:
                    future.set_result(value)
                else:
                    future.set_exception(value)
        except (EOFError, OSError):
            pass
        except Exception:
            log.exception("symbol_worker_reply_unreadable", pid=process.pid)
        finally:
            self._lost(process, conn)

    def _lost(self, process: multiprocessing.process.BaseProcess, conn: Connection) -> None:
        with self._lock:
            if self._process is process:
                self._process = self._conn = self._reader = None
                self._cache_info = None
            pending, self._pending = self._pending, {}
            closed = self._closed
        conn.close()
        process.join(self._grace_s if closed else 1.0)
        if process.is_alive():
            process.kill()
            process.join(1.0)
        if closed:
            error: Exception = OperationCancelled("Symbol worker stopped during shutdown")
        else:
            log.warning("symbol_worker_exited", pid=process.pid, exitcode=process.exitcode)
            error = SymbolWorkerLost(
                f"The symbol worker exited (exit code {process.exitcode}); retry the call."
            )
        for future in pending.values():
            future.set_exception(error)


def _emit_worker_event(pid: int | None, record: dict) -> None:
    """Re-emit a worker log event through the daemon's configured sinks."""
    record = dict(record)
    level = logging.getLevelName(str(record.pop("level", "info")).upper())
    event = record.pop("event", "symbol_worker_event")
    record.pop("timestamp", None)
    log.log(level if isinstance(level, int) else logging.INFO, event, worker_pid=pid, **record)


# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------


def _initialize_worker(conn: Connection, send_lock: threading.Lock, symbols_dir: str) -> None:
    """Prepare the worker before any lgrep module that may log is imported."""
    # The daemon's fd 1 is its stdio MCP channel. Nothing here may write there.
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    # The daemon owns shutdown; a terminal interrupt reaches the whole group.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    level = getattr(logging, os.environ.get("LGREP_LOG_LEVEL", "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))
    root.setLevel(level)

    def forward(_logger: Any, _method: str, event_dict: dict) -> dict:
        # The daemon owns the log sinks, including the locked LGREP_LOG_FILE.
        # Stderr is the fallback when the daemon cannot be reached.
        try:
            record = json.loads(json.dumps(event_dict, default=str))
            with send_lock:
                conn.send(("log", record))
        except (OSError, ValueError, TypeError):
            return event_dict
        raise structlog.DropEvent

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.format_exc_info,
            forward,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )

    from lgrep.memory import cap_malloc_arenas

    cap_malloc_arenas()

    from lgrep.storage import index_store

    index_store.DEFAULT_SYMBOLS_DIR = Path(symbols_dir)


def _portable(exc: BaseException) -> BaseException:
    """Return ``exc`` when it survives a pickle round trip, else a summary.

    The daemon must be able to load the reply: an exception whose constructor
    needs arguments pickles but fails to unpickle, and an unreadable reply
    would make the daemon discard a healthy worker.
    """
    try:
        pickle.loads(pickle.dumps(exc))
    except Exception:
        return RuntimeError(f"{type(exc).__name__}: {exc}"[:500])
    return exc


class _StopSignal:
    """Set by daemon shutdown through the shared event, or by losing the daemon.

    The shared event outlives this worker and reaches its replacement, so
    only the daemon sets it.
    """

    def __init__(self, shared: Any) -> None:
        self._shared = shared
        self._local = threading.Event()

    def set_local(self) -> None:
        self._local.set()

    def is_set(self) -> bool:
        return self._local.is_set() or self._shared.is_set()


def _worker_main(conn: Connection, stop_event: Any, symbols_dir: str) -> None:
    """Serve daemon requests until the daemon asks to exit or disappears."""
    send_lock = threading.Lock()
    _initialize_worker(conn, send_lock, symbols_dir)

    from lgrep.memory import IdleTrimmer
    from lgrep.storage.index_store import IndexStore

    stop = _StopSignal(stop_event)
    operations = SymbolOperations(stop)
    state = threading.Lock()
    running = [0]
    trimmer = IdleTrimmer(lambda: running[0] == 0)

    def execute(request_id: int, op: str, args: tuple, kwargs: dict) -> None:
        try:
            reply = (request_id, True, operations.run(op, args, kwargs))
        except BaseException as exc:  # noqa: BLE001 - every failure returns to its caller
            reply = (request_id, False, _portable(exc))
        with state:
            running[0] -= 1
            idle = running[0] == 0
        try:
            with send_lock:
                try:
                    conn.send(("done", *reply, IndexStore.cache_info()))
                except (pickle.PicklingError, TypeError, AttributeError) as exc:
                    conn.send(("done", request_id, False, _portable(exc), IndexStore.cache_info()))
        except (OSError, ValueError):
            pass  # The daemon is gone; its requests have no reader.
        if idle:
            trimmer.notify_idle()

    pool = ThreadPoolExecutor(max_workers=WORKER_THREADS, thread_name_prefix="lgrep-symbol-worker")
    try:
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                break
            if message[0] == "exit":
                break
            _, request_id, op, args, kwargs = message
            with state:
                running[0] += 1
            pool.submit(execute, request_id, op, args, kwargs)
    finally:
        stop.set_local()
        pool.shutdown(wait=True, cancel_futures=True)
        trimmer.close()
        conn.close()
