"""Bootstrap: server entry point."""

from __future__ import annotations

import sys

import structlog

from lgrep.logging_setup import configure_logging
from lgrep.logging_setup import shutdown_logging as shutdown_logging

# Transport kind recorded by ``run_server`` and read lazily by the lifespan.
# This actual startup value drives diagnostics and explicit-job admission.
# It is not environment configuration: only stdio has a process-lifetime context.
_startup_transport: str | None = None


def get_startup_transport() -> str | None:
    """Return the transport kind recorded by ``run_server``, if any."""
    return _startup_transport


# Tools that refuse every call without LGREP_ALLOW_DESTRUCTIVE_MCP.
GRANT_ONLY_TOOLS = ("invalidate_cache", "invalidate_worktree_cache")
# Explicit symbol-index job tools; they need a process-lifetime stdio context.
STDIO_ONLY_TOOLS = ("index_symbols_folder", "index_symbols_repo", "get_symbol_index_job")


def apply_tool_profile(server, transport: str) -> list[str]:
    """Unregister tools this server could only refuse, and return their names.

    A tool listed to an agent should be able to succeed. The handlers keep
    their own grant and stdio checks, so in-process callers stay refused.
    """
    from lgrep.server.tools_maintenance import _destructive_grant_present

    hidden: list[str] = []
    if not _destructive_grant_present():
        hidden.extend(GRANT_ONLY_TOOLS)
    if transport != "stdio":
        hidden.extend(STDIO_ONLY_TOOLS)
    for name in hidden:
        server.remove_tool(name)
    return hidden


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 6285) -> int:
    """Start the MCP server.

    Args:
        transport: Transport protocol - "stdio" or "streamable-http".
        host: Host to bind to (only for HTTP transport).
        port: Port to bind to (only for HTTP transport).
    """
    global _startup_transport

    # First, so no startup event reaches stdout, the stdio MCP channel.
    configure_logging()

    # Before any worker or LanceDB thread exists, so the cap binds every arena.
    from lgrep.memory import cap_malloc_arenas

    cap_malloc_arenas()

    # Import here to avoid circular imports at module load time
    from lgrep.server import mcp

    log = structlog.get_logger()
    log.info("lgrep_mcp_server_starting", transport=transport, host=host, port=port)

    # Preserve the transport for diagnostics and job admission without exposing it as an
    # environment variable that other code could read or mutate.
    _startup_transport = transport

    hidden = apply_tool_profile(mcp, transport)
    log.info("lgrep_tool_profile", transport=transport, hidden_tools=hidden)

    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(run_server())
