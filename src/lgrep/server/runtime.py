"""Runtime supervision for blocking lgrep daemon work.

The MCP handlers are async, but semantic indexing/search and cache operations
are mostly synchronous.  This module gives those blocking calls one structural
owner: bounded execution plus observable job lifecycle state.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

DEFAULT_WORKER_MAX_THREADS = 4
DEFAULT_HISTORY_LIMIT = 100


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
            "error": self.error,
        }


class RuntimeSupervisor:
    """Owns bounded execution and lifecycle state for blocking work."""

    def __init__(
        self, *, max_workers: int | None = None, history_limit: int = DEFAULT_HISTORY_LIMIT
    ):
        if max_workers is None:
            max_workers = _worker_limit_from_env()
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if history_limit < 1:
            raise ValueError("history_limit must be >= 1")

        self.max_workers = max_workers
        self.history_limit = history_limit
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="lgrep-worker",
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
        **kwargs: Any,
    ) -> T:
        """Run a synchronous function under bounded, observable supervision."""
        job = self._create_job(kind=kind, caller=caller, project=project)

        def invoke() -> T:
            self._mark_started(job.id)
            return fn(*args, **kwargs)

        future = self._executor.submit(invoke)
        with self._lock:
            job.future = future
        future.add_done_callback(
            lambda done_future: self._complete_from_future(job.id, done_future)
        )

        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
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
        """Shut down the executor and mark queued/running jobs honestly."""
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

    def _create_job(self, *, kind: str, caller: str, project: str | None) -> RuntimeJob:
        job_id = f"job-{next(self._counter):08d}"
        job = RuntimeJob(
            id=job_id,
            kind=kind,
            caller=caller,
            project=project,
            status=JobStatus.QUEUED,
            created_at=time.time(),
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


def _worker_limit_from_env() -> int:
    raw = os.environ.get("LGREP_WORKER_MAX_THREADS")
    if not raw:
        return DEFAULT_WORKER_MAX_THREADS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WORKER_MAX_THREADS
    return max(1, value)


def _summarize_exception(exc: BaseException) -> str:
    """Return bounded, non-traceback error text for diagnostics."""
    message = str(exc)
    summary = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return summary[:500]
