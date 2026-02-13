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
    if args and args[0] == "search":
        return _cmd_search(args[1:])

    if args and args[0] == "index":
        return _cmd_index(args[1:])

    if args and args[0] == "remove":
        return _cmd_remove(args[1:])

    if args and args[0] == "install-opencode":
        from lgrep.install_opencode import install

        return install()

    if args and args[0] == "uninstall-opencode":
        from lgrep.install_opencode import uninstall

        return uninstall()

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
    print("  search <query> [path]          search indexed project (one-shot, no server)")
    print("  index [path]                   index a project for semantic search")
    print("  remove <path>                  show project index info")
    print("  install-opencode               install lgrep into OpenCode (tool + MCP + skill)")
    print("  uninstall-opencode             remove lgrep from OpenCode")
    print()
    print("search options:")
    print("  -m, --limit N                  max results (default: 10)")
    print("  --no-hybrid                    vector-only search (skip keyword matching)")
    print()
    print("index options:")
    print("  --chunk-size N                 token size per chunk (default: 500)")
    print()
    print("server options:")
    print("  --version                      show version and exit")
    print("  --help                         show this help and exit")
    print("  --transport {stdio,streamable-http}")
    print("                                 transport protocol (default: stdio)")
    print("  --port PORT                    port for HTTP transport (default: 6285)")
    print("  --host HOST                    host for HTTP transport (default: 127.0.0.1)")


def _cmd_search(args: list[str]) -> int:
    """One-shot semantic search against an already-indexed project.

    Bypasses the MCP server entirely — creates an embedder and ChunkStore
    directly, embeds the query, runs hybrid search, and prints JSON to stdout.

    Usage: lgrep search <query> [path] [-m N] [--no-hybrid]
    """
    if "--help" in args or "-h" in args:
        print("usage: lgrep search <query> [path] [-m N] [--no-hybrid]")
        print()
        print("Search an indexed project using semantic code search.")
        print()
        print("arguments:")
        print("  query                          natural language search query")
        print("  path                           project path (default: current directory)")
        print()
        print("options:")
        print("  -m, --limit N                  max results (default: 10)")
        print("  --no-hybrid                    vector-only search (skip keyword matching)")
        return 0

    import os
    from dataclasses import asdict
    from pathlib import Path

    from lgrep.embeddings import VoyageEmbedder
    from lgrep.storage import ChunkStore, get_project_db_path

    # Parse args
    query = None
    path = None
    limit = 10
    hybrid = True
    positional = []

    i = 0
    while i < len(args):
        if args[i] in ("-m", "--limit") and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--no-hybrid":
            hybrid = False
            i += 1
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1
        else:
            positional.append(args[i])
            i += 1

    if not positional:
        print("Error: query is required. Usage: lgrep search <query> [path]", file=sys.stderr)
        return 1

    query = positional[0]
    path = Path(positional[1]).resolve() if len(positional) > 1 else Path.cwd().resolve()

    # Validate environment
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "error": "VOYAGE_API_KEY not set",
                    "hint": "Set VOYAGE_API_KEY in your environment or MCP server config env section.",
                }
            )
        )
        return 1

    # Validate index exists
    db_path = get_project_db_path(path)
    if not db_path.exists():
        print(
            json.dumps(
                {
                    "error": f"No index found for {path}. Run 'lgrep index {path}' first.",
                    "hint": "The lgrep MCP server auto-indexes on first search. Use the MCP tool (lgrep_search) instead of the CLI wrapper for automatic indexing.",
                }
            )
        )
        return 1

    # Search
    try:
        embedder = VoyageEmbedder(api_key=api_key)
        store = ChunkStore(db_path)

        query_vector = embedder.embed_query(query)

        if hybrid:
            results = store.search_hybrid(query_vector, query, limit)
        else:
            results = store.search_vector(query_vector, limit)

        print(json.dumps(asdict(results)))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


def _cmd_index(args: list[str]) -> int:
    """Index a project directory for semantic search.

    Creates an embedder and ChunkStore directly, performs a full index,
    and prints JSON status to stdout. Bypasses the MCP server entirely.

    Usage: lgrep index [path] [--chunk-size N]
    """
    if "--help" in args or "-h" in args:
        print("usage: lgrep index [path] [--chunk-size N]")
        print()
        print("Index a project directory for semantic code search.")
        print()
        print("arguments:")
        print("  path                           project path (default: current directory)")
        print()
        print("options:")
        print("  --chunk-size N                 token size per chunk (default: 500)")
        return 0

    import os
    from pathlib import Path

    from lgrep.embeddings import VoyageEmbedder
    from lgrep.indexing import Indexer
    from lgrep.storage import ChunkStore, get_project_db_path

    # Parse args
    chunk_size = 500
    positional = []

    i = 0
    while i < len(args):
        if args[i] == "--chunk-size" and i + 1 < len(args):
            chunk_size = int(args[i + 1])
            i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1
        else:
            positional.append(args[i])
            i += 1

    path = Path(positional[0]).resolve() if positional else Path.cwd().resolve()

    # Validate path
    if not path.exists() or not path.is_dir():
        print(json.dumps({"error": f"Path does not exist or is not a directory: {path}"}))
        return 1

    # Validate environment
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "error": "VOYAGE_API_KEY not set",
                    "hint": "Set VOYAGE_API_KEY in your environment or MCP server config env section.",
                }
            )
        )
        return 1

    # Index
    try:
        db_path = get_project_db_path(path)
        embedder = VoyageEmbedder(api_key=api_key)
        store = ChunkStore(db_path)
        indexer = Indexer(path, store, embedder, chunk_size=chunk_size)

        status = indexer.index_all()

        print(
            json.dumps(
                {
                    "project": str(path),
                    "file_count": status.file_count,
                    "chunk_count": status.chunk_count,
                    "duration_ms": round(status.duration_ms, 2),
                    "total_tokens": status.total_tokens,
                }
            )
        )
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1


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
