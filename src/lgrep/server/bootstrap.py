"""Bootstrap: server entry point."""

from __future__ import annotations

import logging
import os
import sys

import structlog

# Transport kind recorded by ``run_server`` and read lazily by the lifespan.
# Kept as a module attribute rather than an environment variable so diagnostics
# can report the actual startup transport without creating a side channel that
# other code could mistake for configuration.
_startup_transport: str | None = None


def get_startup_transport() -> str | None:
    """Return the transport kind recorded by ``run_server``, if any."""
    return _startup_transport


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

    # Configure structlog for JSON output
    log_level = getattr(
        logging,
        os.environ.get("LGREP_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
    )

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
