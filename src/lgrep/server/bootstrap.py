"""Bootstrap: server entry point."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog

# Transport kind recorded by ``run_server`` and read lazily by the lifespan.
# Kept as a module attribute rather than an environment variable so diagnostics
# can report the actual startup transport without creating a side channel that
# other code could mistake for configuration.
_startup_transport: str | None = None


def get_startup_transport() -> str | None:
    """Return the transport kind recorded by ``run_server``, if any."""
    return _startup_transport


# Fixed rotation bounds for the opt-in ``LGREP_LOG_FILE`` sink.
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3

# Handlers installed by ``configure_logging`` and the fd holding the
# single-writer log lock. Tracked so reconfiguration is idempotent and
# ``shutdown_logging`` can release everything it owns.
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
    """Attach the ``LGREP_LOG_FILE`` rotating sink when its lock is won.

    One writer is enforced by an exclusive non-blocking flock on the
    ``<file>.lock`` sidecar, held for process life: flock locks survive
    RotatingFileHandler renames and make the CPython "one file, many
    processes is unsupported" constraint structural. A loser warns on
    stderr and keeps stderr-only logging.
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


def configure_logging() -> None:
    """Configure structlog through stdlib logging for the MCP server.

    Takes over the root handler set: every handler installed before this
    call is removed, so structured events reach exactly one formatted
    stderr handler (plus the optional locked file sink) and never a
    preinstalled stdout handler. stdout is never a log sink: the stdio
    MCP channel owns it.
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
    """Remove and close handlers installed by ``configure_logging``."""
    _remove_owned_handlers()
    _release_log_lock()


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 6285) -> int:
    """Start the MCP server.

    Args:
        transport: Transport protocol - "stdio" or "streamable-http".
        host: Host to bind to (only for HTTP transport).
        port: Port to bind to (only for HTTP transport).
    """
    global _startup_transport

    # Import here to avoid circular imports at module load time
    from lgrep.server import mcp

    configure_logging()

    log = structlog.get_logger()
    log.info("lgrep_mcp_server_starting", transport=transport, host=host, port=port)

    # Preserve the startup transport for diagnostics without exposing it as an
    # environment variable that other code could read or mutate.
    _startup_transport = transport

    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(run_server())
