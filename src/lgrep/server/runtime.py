"""Runtime supervision for blocking lgrep daemon work.

The MCP handlers are async, but semantic indexing/search and cache operations
are mostly synchronous.  This module gives those blocking calls one structural
owner: bounded execution plus observable job lifecycle state.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import os
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

log = structlog.get_logger(__name__)

# Correlation id for the MCP tool call that started a job. Set by
# ``time_tool`` on the event loop and read by ``_create_job`` in the calling
# coroutine's context, so ``run_blocking`` callers need no extra parameter.
call_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lgrep_call_id", default=None
)

DEFAULT_WORKER_MAX_THREADS = 4
DEFAULT_BUILD_MAX_THREADS = 1
DEFAULT_HISTORY_LIMIT = 100

QUERY_LANE = "query"
BUILD_LANE = "build"

# Job kinds whose work is build or maintenance (index windows, pending-file
# computation, full re-index, orphan/prune sweeps, remote repo indexing).
# They run on the dedicated build executor so a build window cannot occupy
# the worker threads a query needs. Every other kind runs on the query
# executor sized by LGREP_WORKER_MAX_THREADS.
BUILD_JOB_KINDS = frozenset(
    {
        "index_window",
        "compute_pending_files",
        "index_all",
        "startup_orphan_sweep",
        "prune_orphans",
        "prune_symbols",
        "index_repo",
    }
)


def _lane_for_kind(kind: str) -> str:
    """Return the executor lane a job kind runs on by default."""
    return BUILD_LANE if kind in BUILD_JOB_KINDS else QUERY_LANE


class JobStatus(StrEnum):
    """Lifecycle state for a blocking daemon job."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    FINISHED_AFTER_ABANDON = "finished_after_abandon"
    FAILED_AFTER_ABANDON = "failed_after_abandon"


TERMINAL_STATUSES = frozenset(
    {
        JobStatus.FINISHED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.FINISHED_AFTER_ABANDON,
        JobStatus.FAILED_AFTER_ABANDON,
    }
)


@dataclass
class RuntimeJob:
    """Mutable in-memory record for one blocking daemon job."""

    id: str
    kind: str
    caller: str
    project: str | None
    status: JobStatus
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    abandoned: bool = False
    call_id: str | None = None
    lane: str = QUERY_LANE
    future: Future[Any] | None = None

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """Return an operator-safe diagnostic representation."""
        now = time.time() if now is None else now
        age_ms = round((now - self.created_at) * 1000, 2)
        duration_ms = None
        if self.finished_at is not None:
            duration_ms = round((self.finished_at - self.created_at) * 1000, 2)
        return {
            "id": self.id,
            "kind": self.kind,
            "caller": self.caller,
            "project": self.project,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "age_ms": age_ms,
            "duration_ms": duration_ms,
            "abandoned": self.abandoned,
            "lane": self.lane,
            "error": self.error,
        }


class RuntimeSupervisor:
    """Owns bounded execution and lifecycle state for blocking work.

    Two executors: the query lane (``lgrep-worker`` threads, sized by
    ``LGREP_WORKER_MAX_THREADS``) serves tool-call work, and the build lane
    (``lgrep-build`` threads, sized by ``LGREP_BUILD_MAX_THREADS``) serves
    build/maintenance kinds so they cannot occupy query threads.
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        max_build_workers: int | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ):
        if max_workers is None:
            max_workers = _worker_limit_from_env()
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_build_workers is None:
            max_build_workers = _build_limit_from_env()
        if max_build_workers < 1:
            raise ValueError("max_build_workers must be >= 1")
        if history_limit < 1:
            raise ValueError("history_limit must be >= 1")

        self.max_workers = max_workers
        self.max_build_workers = max_build_workers
        self.history_limit = history_limit
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="lgrep-worker",
        )
        self._build_executor = ThreadPoolExecutor(
            max_workers=max_build_workers,
            thread_name_prefix="lgrep-build",
        )
        self._counter = itertools.count(1)
        self._lock = threading.RLock()
        self._active: dict[str, RuntimeJob] = {}
        self._recent: deque[RuntimeJob] = deque(maxlen=history_limit)
        self.started_at = time.time()

    async def run_blocking(
        self,
        kind: str,
        caller: str,
        project: str | None,
        fn: Callable[..., T],
        *args: Any,
        cancel_event: threading.Event | None = None,
        lane: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Run a synchronous function under bounded, observable supervision.

        Args:
            kind: Job kind label for diagnostics (e.g. "index_all", "search_vector").
            caller: Tool name that initiated the work.
            project: Project path, if applicable.
            fn: The synchronous function to execute.
            *args: Positional args forwarded to ``fn``.
            cancel_event: Optional cooperative-cancellation primitive. If
                the awaiting asyncio coroutine is cancelled, the supervisor
                calls ``cancel_event.set()`` BEFORE propagating the
                ``CancelledError``, so the blocking thread can observe
                the signal at the next safe point and unwind.
            lane: Executor lane for the job: "query" or "build". Defaults
                to the lane the job kind maps to (see ``BUILD_JOB_KINDS``).
            **kwargs: Keyword args forwarded to ``fn`` (not including
                ``cancel_event`` or ``lane``).
        """
        resolved_lane = lane if lane is not None else _lane_for_kind(kind)
        if resolved_lane not in (QUERY_LANE, BUILD_LANE):
            raise ValueError(f"unknown lane: {resolved_lane!r}")
        executor = self._executor if resolved_lane == QUERY_LANE else self._build_executor
        job = self._create_job(kind=kind, caller=caller, project=project, lane=resolved_lane)

        def invoke() -> T:
            self._mark_started(job.id)
            return fn(*args, **kwargs)

        future = executor.submit(invoke)
        with self._lock:
            job.future = future
        future.add_done_callback(
            lambda done_future: self._complete_from_future(job.id, done_future)
        )

        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            # Propagate cancellation to the blocking work BEFORE we mark
            # the job abandoned, so the work has a chance to exit cleanly
            # at its next cooperative-cancellation check point. Without
            # this, blocking work that cannot be interrupted mid-call
            # (e.g. LanceDB I/O) holds the worker thread forever and the
            # bounded executor pool fills with abandoned threads.
            if cancel_event is not None:
                cancel_event.set()
            self._mark_cancelled_or_abandoned(job.id)
            raise

    def snapshot_active_jobs(self) -> list[dict[str, Any]]:
        """Return active/non-terminal jobs for diagnostics."""
        now = time.time()
        with self._lock:
            return [job.snapshot(now=now) for job in self._active.values()]

    def snapshot_recent_jobs(self) -> list[dict[str, Any]]:
        """Return bounded terminal job history for diagnostics."""
        now = time.time()
        with self._lock:
            return [job.snapshot(now=now) for job in self._recent]

    def shutdown(self, *, cancel_futures: bool = True) -> None:
        """Shut down both executors and mark queued/running jobs honestly."""
        with self._lock:
            active_jobs = list(self._active.values())
        for job in active_jobs:
            future = job.future
            if future is not None and future.cancel():
                self._finish_job(job.id, JobStatus.CANCELLED)
            elif job.status not in TERMINAL_STATUSES:
                with self._lock:
                    if job.id in self._active and job.status not in TERMINAL_STATUSES:
                        job.status = JobStatus.CANCEL_REQUESTED
        self._executor.shutdown(wait=False, cancel_futures=cancel_futures)
        self._build_executor.shutdown(wait=False, cancel_futures=cancel_futures)

    def _create_job(
        self, *, kind: str, caller: str, project: str | None, lane: str = QUERY_LANE
    ) -> RuntimeJob:
        job_id = f"job-{next(self._counter):08d}"
        job = RuntimeJob(
            id=job_id,
            kind=kind,
            caller=caller,
            project=project,
            status=JobStatus.QUEUED,
            created_at=time.time(),
            call_id=call_id_var.get(),
            lane=lane,
        )
        with self._lock:
            self._active[job.id] = job
        return job

    def _mark_started(self, job_id: str) -> None:
        with self._lock:
            job = self._active.get(job_id)
            if job is not None and job.status == JobStatus.QUEUED:
                job.status = JobStatus.RUNNING
                job.started_at = time.time()

    def _mark_cancelled_or_abandoned(self, job_id: str) -> None:
        with self._lock:
            job = self._active.get(job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return
            future = job.future
            if future is not None and future.cancel():
                terminal = JobStatus.CANCELLED
            else:
                terminal = None
                job.status = JobStatus.ABANDONED
                job.abandoned = True

        if terminal is not None:
            self._finish_job(job_id, terminal)

    def _complete_from_future(self, job_id: str, future: Future[Any]) -> None:
        if future.cancelled():
            self._finish_job(job_id, JobStatus.CANCELLED)
            return

        error: str | None = None
        try:
            future.result()
        except BaseException as exc:  # noqa: BLE001 — diagnostics need bounded summary for all failures
            error = _summarize_exception(exc)

        with self._lock:
            job = self._active.get(job_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return
            abandoned = job.abandoned or job.status == JobStatus.ABANDONED

        if error is not None:
            status = JobStatus.FAILED_AFTER_ABANDON if abandoned else JobStatus.FAILED
        else:
            status = JobStatus.FINISHED_AFTER_ABANDON if abandoned else JobStatus.FINISHED
        self._finish_job(job_id, status, error=error)

    def _finish_job(self, job_id: str, status: JobStatus, error: str | None = None) -> None:
        with self._lock:
            job = self._active.pop(job_id, None)
            if job is None:
                return
            job.status = status
            job.finished_at = time.time()
            if error is not None:
                job.error = error
            if status in {JobStatus.FINISHED_AFTER_ABANDON, JobStatus.FAILED_AFTER_ABANDON}:
                job.abandoned = True
            self._recent.append(job)
            queue_ms = (
                round((job.started_at - job.created_at) * 1000, 2)
                if job.started_at is not None
                else None
            )
            run_ms = (
                round((job.finished_at - job.started_at) * 1000, 2)
                if job.started_at is not None
                else None
            )
            total_ms = round((job.finished_at - job.created_at) * 1000, 2)
            fields = {
                "job_id": job.id,
                "call_id": job.call_id,
                "kind": job.kind,
                "caller": job.caller,
                "project": job.project,
                "status": status.value,
                "queue_ms": queue_ms,
                "run_ms": run_ms,
                "total_ms": total_ms,
                "abandoned": job.abandoned,
                "lane": job.lane,
                "error": job.error,
            }
        log.info("runtime_job_finished", **fields)


def _worker_limit_from_env() -> int:
    raw = os.environ.get("LGREP_WORKER_MAX_THREADS")
    if not raw:
        return DEFAULT_WORKER_MAX_THREADS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WORKER_MAX_THREADS
    return max(1, value)


def _build_limit_from_env() -> int:
    raw = os.environ.get("LGREP_BUILD_MAX_THREADS")
    if not raw:
        return DEFAULT_BUILD_MAX_THREADS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BUILD_MAX_THREADS
    return max(1, value)


def _summarize_exception(exc: BaseException) -> str:
    """Return bounded, non-traceback error text for diagnostics."""
    message = str(exc)
    summary = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return summary[:500]
