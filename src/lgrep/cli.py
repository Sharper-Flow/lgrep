"""lgrep CLI entry point."""

import json
import sys


def main() -> int:
    """CLI entry point for lgrep.

    The primary interface is the MCP server (default command).
    Management subcommands are available for server administration.
    """
    # Handle --version before importing heavy deps
    if "--version" in sys.argv:
        from lgrep import __version__

        print(f"lgrep {__version__}")
        return 0

    args = sys.argv[1:]

    # Subcommand dispatch
    if args and args[0] == "remove":
        return _cmd_remove(args[1:])

    if "--help" in sys.argv or "-h" in sys.argv:
        _print_help()
        return 0

    # Parse transport args (default: start MCP server)
    transport = "stdio"
    host = "127.0.0.1"
    port = 6285

    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            return 1

    if transport not in ("stdio", "streamable-http"):
        print(f"Invalid transport: {transport}. Use 'stdio' or 'streamable-http'.", file=sys.stderr)
        return 1

    # Default: start MCP server
    from lgrep.server import run_server

    return run_server(transport=transport, host=host, port=port)


def _print_help() -> None:
    """Print CLI help text."""
    print("usage: lgrep [command] [options]")
    print()
    print("Semantic code search MCP server.")
    print()
    print("commands:")
    print("  (default)                      start MCP server")
    print("  remove <path>                  show project index info")
    print()
    print("server options:")
    print("  --version                      show version and exit")
    print("  --help                         show this help and exit")
    print("  --transport {stdio,streamable-http}")
    print("                                 transport protocol (default: stdio)")
    print("  --port PORT                    port for HTTP transport (default: 6285)")
    print("  --host HOST                    host for HTTP transport (default: 127.0.0.1)")


def _cmd_remove(args: list[str]) -> int:
    """Show project index info (management command).

    The in-memory eviction happens via remove_project() in server.py,
    which requires a running server context. This CLI command shows
    the on-disk state for diagnostics.
    """
    if not args or "--help" in args:
        print("usage: lgrep remove <path>")
        print()
        print("Show on-disk index info for a project.")
        print("To evict from a running server, restart the server process.")
        return 0 if "--help" in args else 1

    from pathlib import Path

    from lgrep.storage import get_project_db_path

    path = args[0]
    project_path = Path(path).resolve()
    db_path = get_project_db_path(project_path)

    if db_path.exists():
        print(
            json.dumps(
                {
                    "project": str(project_path),
                    "db_path": str(db_path),
                    "db_exists": True,
                    "message": "Project has on-disk index. Restart server to evict from memory.",
                },
                indent=2,
            )
        )
    else:
        print(
            json.dumps(
                {
                    "project": str(project_path),
                    "db_path": str(db_path),
                    "db_exists": False,
                    "message": "No index found for this project.",
                },
                indent=2,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
