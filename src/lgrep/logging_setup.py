"""Shared structured logging configuration for server and CLI entry points."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog

LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3

_owned_handlers: list[logging.Handler] = []
_log_lock_fd: int | None = None


def _release_log_lock() -> None:
    global _log_lock_fd
    if _log_lock_fd is not None:
        with contextlib.suppress(OSError):
            os.close(_log_lock_fd)
        _log_lock_fd = None


def _remove_owned_handlers() -> None:
    root = logging.getLogger()
    for handler in _owned_handlers:
        root.removeHandler(handler)
        handler.close()
    _owned_handlers.clear()


def _try_attach_file_sink() -> None:
    """Attach the optional rotating sink only while holding its writer lock.

    The lock uses a separate sidecar so rotation cannot change its identity.
    A losing writer warns on stderr and keeps stderr-only logging.
    """
    global _log_lock_fd

    log_file = os.environ.get("LGREP_LOG_FILE")
    if not log_file:
        return

    try:
        lock_fd = os.open(f"{log_file}.lock", os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        print(
            f"lgrep: cannot open lock sidecar {log_file}.lock ({exc.strerror}); "
            f"keeping stderr-only logging",
            file=sys.stderr,
        )
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(lock_fd)
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            reason = "is held by another process"
        else:
            reason = f"could not be locked ({exc.strerror})"
        print(
            f"lgrep: LGREP_LOG_FILE {log_file} {reason} "
            f"(lock {log_file}.lock); keeping stderr-only logging",
            file=sys.stderr,
        )
        return

    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
        )
    except OSError:
        os.close(lock_fd)
        print(
            f"lgrep: cannot open LGREP_LOG_FILE {log_file}; keeping stderr-only logging",
            file=sys.stderr,
        )
        return

    _log_lock_fd = lock_fd
    _owned_handlers.append(file_handler)
    logging.getLogger().addHandler(file_handler)


def configure_logging(*, file_sink: bool = True) -> None:
    """Route structured events to stderr and optionally the locked daemon sink.

    Replace existing root handlers so none can leak logs into stdout. CLI
    commands disable the file sink: the daemon owns its single-writer lock.
    """
    log_level = getattr(
        logging,
        os.environ.get("LGREP_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )

    _remove_owned_handlers()
    _release_log_lock()

    root = logging.getLogger()
    root.setLevel(log_level)
    for foreign_handler in root.handlers[:]:
        root.removeHandler(foreign_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    _owned_handlers.append(stderr_handler)
    root.addHandler(stderr_handler)

    if file_sink:
        _try_attach_file_sink()

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
        ],
    )
    for handler in _owned_handlers:
        handler.setFormatter(formatter)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def shutdown_logging() -> None:
    """Remove owned handlers and release the optional file-sink lock."""
    _remove_owned_handlers()
    _release_log_lock()
