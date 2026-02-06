"""lgrep CLI entry point."""

import sys


def main() -> int:
    """CLI entry point for lgrep.

    The primary interface is the MCP server (serve command).
    Use --version to check the installed version.
    """
    # Handle --version before importing heavy deps
    if "--version" in sys.argv:
        from lgrep import __version__

        print(f"lgrep {__version__}")
        return 0

    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: lgrep [--version] [--help]")
        print()
        print("Semantic code search MCP server.")
        print("Starts the MCP server on stdio transport.")
        print()
        print("options:")
        print("  --version  show version and exit")
        print("  --help     show this help and exit")
        return 0

    # Default: start MCP server
    from lgrep.server import run_server

    return run_server()


if __name__ == "__main__":
    sys.exit(main())
