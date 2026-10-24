"""Native heap bounds for the long-running lgrep daemon.

LanceDB scans run on many native threads. Under glibc each thread may get its
own malloc arena (up to 8 per core by default), and memory freed after a scan
stays in fragmented arenas instead of returning to the OS. The daemon caps the
arena count once at startup and trims free heap when the runtime goes idle.
Both hooks are no-ops when the C library is not glibc.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

log = structlog.get_logger(__name__)

# glibc mallopt parameter number for M_ARENA_MAX (malloc.h).
_M_ARENA_MAX = -8

# Arena cap applied at server startup. A bounded probe of repeated owner-store
# scans held RSS flat with a cap of 2 or 4 and idle trimming; 4 leaves headroom
# for the daemon's six bounded lane threads.
ARENA_MAX = 4

# Minimum seconds between idle trims; one trim of a fragmented heap took up to
# about 100 ms in the probe.
TRIM_MIN_INTERVAL_S = 30.0

_lock = threading.Lock()
_libc: ctypes.CDLL | None = None
_libc_loaded = False
_arena_max: int | None = None
_trim_count = 0


def _glibc() -> ctypes.CDLL | None:
    """Return the process's glibc handle, or None for any other C library."""
    global _libc, _libc_loaded
    if _libc_loaded:
        return _libc
    _libc_loaded = True
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        libc.gnu_get_libc_version  # noqa: B018 - glibc-only symbol probe
        libc.mallopt.argtypes = [ctypes.c_int, ctypes.c_int]
        libc.malloc_trim.argtypes = [ctypes.c_size_t]
    except (OSError, AttributeError):
        return None
    _libc = libc
    return libc


def cap_malloc_arenas(arena_max: int = ARENA_MAX) -> bool:
    """Cap glibc malloc arenas; call before worker and native threads start.

    glibc fixes its arena limit the first time it needs an arena beyond its
    test count, so a later call may not bound arenas already created.
    Returns whether the cap was applied.
    """
    global _arena_max
    libc = _glibc()
    if libc is None:
        return False
    if libc.mallopt(_M_ARENA_MAX, arena_max) != 1:
        log.warning("malloc_arena_cap_failed", arena_max=arena_max)
        return False
    with _lock:
        _arena_max = arena_max
    log.info("malloc_arena_cap_applied", arena_max=arena_max)
    return True


def trim_heap() -> bool:
    """Return free heap pages of every arena to the OS. Returns whether it ran."""
    global _trim_count
    libc = _glibc()
    if libc is None:
        return False
    libc.malloc_trim(0)
    with _lock:
        _trim_count += 1
    return True


def allocator_info() -> tuple[int | None, int]:
    """Return ``(applied arena cap or None, completed heap trims)``."""
    with _lock:
        return _arena_max, _trim_count


class IdleTrimmer:
    """Trim the heap after work goes idle, at most once per interval.

    ``notify_idle`` is called whenever the last active job finishes, from a
    worker thread or the event loop. The trim always runs on its own timer
    thread, delayed to the end of the interval after the previous trim, and
    only if work is still idle when the timer fires.
    """

    def __init__(
        self,
        is_idle: Callable[[], bool],
        *,
        interval_s: float = TRIM_MIN_INTERVAL_S,
        trim: Callable[[], bool] = trim_heap,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._is_idle = is_idle
        self._interval_s = interval_s
        self._trim = trim
        self._clock = clock
        self._lock = threading.Lock()
        self._last: float | None = None
        self._timer: threading.Timer | None = None
        self._closed = False

    def notify_idle(self) -> None:
        """Schedule one trim unless one is already pending."""
        with self._lock:
            if self._closed or self._timer is not None:
                return
            wait = 0.0 if self._last is None else self._last + self._interval_s - self._clock()
            self._timer = threading.Timer(max(0.0, wait), self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or not self._is_idle():
                return
            self._last = self._clock()
        start = time.perf_counter()
        if self._trim():
            log.debug("heap_trimmed", ms=round((time.perf_counter() - start) * 1000, 2))

    def close(self) -> None:
        """Cancel a pending deferred trim and stop trimming."""
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def process_rss_swap_bytes() -> tuple[int | None, int | None]:
    """Return this process's resident and swapped bytes from /proc, if readable."""
    rss = swap = None
    try:
        with open(f"/proc/{os.getpid()}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                elif line.startswith("VmSwap:"):
                    swap = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None, None
    return rss, swap
