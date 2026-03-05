# lgrep

**Dual-engine code intelligence for [OpenCode](https://github.com/opencode-ai/opencode).**

Two complementary search engines in one MCP server:

- **Semantic engine** — Find code by *meaning*. "Authentication flow" → `jwt.verify()`. Uses Voyage Code 3 embeddings (92% retrieval quality) with local LanceDB storage.
- **Symbol engine** — Find code by *structure*. Exact function/class/method lookup by name, file outline, repo outline, text search. Uses tree-sitter AST parsing with local JSON index. No API key required.

Your code never leaves your machine — only semantic search queries hit the Voyage API.

## Inspiration

`grep` is one of the most important tools ever built for developers. Forty years later, we still reach for it (or ripgrep) dozens of times a day. But grep searches text, not meaning. When you're working with AI agents that think in concepts — "find the authentication flow", "where is rate limiting handled" — keyword matching falls short.

Semantic code search exists, but every option we found required either uploading your code to someone else's servers, paying a recurring subscription, or accepting significantly worse search quality with local models. We wanted something different: a first-class grep replacement that understands code semantically, keeps everything local, and doesn't cost $20/month to run.

`lgrep` is our answer. It's built for developers and AI agents who want the best available search quality without a cloud dependency for their codebase. Your vectors live on your machine. Your code never leaves your disk. The only thing that touches the network is the short natural language query you're searching for.

We also built it because we run multiple AI coding agents simultaneously and needed a shared semantic index they could all query without stepping on each other. Nothing we found supported that well.

## Why lgrep?

The existing options all have tradeoffs we didn't want to accept:

- **grep / ripgrep** — fast and local, but keyword-only. Can't find conceptually related code. Searching for "error handling in API routes" returns nothing if the code uses `try/catch` with `res.status(500)`.
- **[mgrep](https://github.com/mixedbread-ai/mgrep)** — semantic search that works, but uploads all your code to Mixedbread's cloud servers, costs $15-30/month, and uses embeddings that top out at ~85% retrieval quality.
- **Local embedding models** — fully private, but quality drops to 78% with CPU inference. The gap between 78% and 92% is the difference between useful results and noise.

We wanted the best retrieval quality available, at low cost, without handing our codebase to a third party.

### lgrep vs mgrep vs grep

| | grep / rg | mgrep | **lgrep** |
|---|-----------|-------|-----------|
| **Search type** | Keyword / regex | Semantic | **Semantic + symbol + text** |
| **Retrieval quality** | -- | ~85% | **92% (semantic)** |
| **Monthly cost** | $0 | ~$15-30 | **~$3** |
| **Query latency** | <10ms | ~170ms | **~110ms (semantic), <5ms (symbol)** |
| **Code privacy** | Local | Uploaded to cloud | **Local** (only queries sent to API) |
| **Vectors** | N/A | Cloud | **Local** |
| **Multi-agent** | N/A | Single user | **3+ concurrent agents** |
| **Offline** | Yes | No | Symbol engine: Yes; Semantic: No |
| **Web search** | No | Yes | No |
| **Setup effort** | None | Minimal | Moderate (API key for semantic) |
| **Maturity** | Decades | Production | **Production (v2.0)** |

## How it works

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Agent 1   │  │   Agent 2   │  │   Agent 3   │
│ (OpenCode)  │  │ (OpenCode)  │  │ (OpenCode)  │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        │ MCP (HTTP)
                        ▼
         ┌──────────────────────────────┐
         │      lgrep MCP Server        │
         │      (Python, always warm)   │
         └──────────────┬───────────────┘
                        │
         ┌──────────────┴──────────────┐
         │                             │
         ▼                             ▼
┌─────────────────────┐    ┌─────────────────────┐
│  SEMANTIC ENGINE    │    │   SYMBOL ENGINE     │
│  Voyage Code 3      │    │   tree-sitter AST   │
│  + LanceDB Local    │    │   + JSON index      │
│                     │    │                     │
│  $0.18/1M tokens    │    │   Free (no API)     │
│  92% quality        │    │   <5ms lookup       │
│  32K context        │    │   165+ languages    │
└─────────────────────┘    └─────────────────────┘
```

**Semantic engine:**
1. **Index** — Discover files (respecting `.gitignore`), chunk with AST-aware tree-sitter boundaries, embed via Voyage, store vectors locally in LanceDB.
2. **Search** — Embed the query via Voyage (~90ms), run hybrid search combining vector similarity + BM25 keyword matching with RRF reranking (~15ms), return ranked results.
3. **Watch** — Background file watcher triggers incremental re-indexing on save. Unchanged files are skipped using SHA-256 hashes.

**Symbol engine:**
1. **Index** — Walk source files, parse AST with tree-sitter, extract functions/classes/methods, store in local JSON index.
2. **Search** — Substring match on symbol names, exact lookup by ID, file/repo outline, text search. No API call needed.

### Key design choices

- **AST-aware chunking** via [Chonkie](https://docs.chonkie.ai) + tree-sitter. Functions, classes, and methods become natural chunk boundaries. 28-65% better recall than fixed-size chunking.
- **Hybrid search** combines vector similarity with BM25 keyword matching, reranked with Reciprocal Rank Fusion. Catches both semantic and exact matches.
- **SHA-256 skip optimization** — re-indexing only re-embeds files that actually changed, saving API tokens.
- **Concurrent access** — single warm MCP server process handles 3+ agents querying simultaneously.
- **Symbol IDs** — deterministic `file:kind:name` format (e.g. `src/auth.py:function:authenticate`). Stable across re-indexes, breaks only on rename.

## Installation

```bash
pip install git+https://github.com/Sharper-Flow/lgrep.git
```

Or from source:

```bash
git clone https://github.com/Sharper-Flow/lgrep.git
cd lgrep
pip install .
```

## Setup

### 1. Get a Voyage AI API key (semantic engine only)

Sign up at [dash.voyageai.com](https://dash.voyageai.com/) and create an API key. Voyage offers a free tier of 200M tokens (~5 full codebase indexes).

The symbol engine works without any API key.

Where to set the key:
- **Vision / open-chad setup:** `~/.config/vision/servers.yaml` under `servers.lgrep.env.VOYAGE_API_KEY`
- **Raw OpenCode MCP setup:** `~/.config/opencode/opencode.json` under `mcp.lgrep.env.VOYAGE_API_KEY`

### 2. Start lgrep as a shared server

lgrep runs as a single HTTP server shared across all OpenCode sessions. This means opening 5 sessions doesn't spawn 5 lgrep processes — one ~400MB process handles everything.

**Recommended: systemd user service (auto-starts on login, restarts on crash)**

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/lgrep.service << 'EOF'
[Unit]
Description=lgrep MCP server (dual-engine code intelligence)
After=network.target

[Service]
Type=simple
ExecStart=/path/to/lgrep --transport streamable-http --port 6285
Restart=on-failure
RestartSec=5
Environment=VOYAGE_API_KEY=your-api-key-here
Environment=LGREP_WARM_PATHS=/path/to/project-a:/path/to/project-b
StandardOutput=append:/tmp/lgrep.log
StandardError=append:/tmp/lgrep.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now lgrep.service
```

Replace `/path/to/lgrep` with the output of `which lgrep`.

**Alternative: run manually**

```bash
VOYAGE_API_KEY=your-key \
LGREP_WARM_PATHS=/path/to/project \
lgrep --transport streamable-http --port 6285
```

Or use `lgrep install-opencode` to get the full setup printed for you.

### 3. Configure OpenCode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "lgrep": {
      "type": "remote",
      "url": "http://localhost:6285/mcp",
      "enabled": true
    }
  }
}
```

Or run `lgrep install-opencode` to write this automatically.

### 4. Search

```
# Semantic: find by meaning
lgrep_search_semantic(query="authentication flow", path="/path/to/your/project")

# Symbol: find by name (index first)
lgrep_index_symbols_folder(path="/path/to/your/project")
lgrep_search_symbols(query="authenticate", path="/path/to/your/project")

# Symbol: get file outline (no index needed)
lgrep_get_file_outline(path="/path/to/your/project/src/auth.py")
```

### 5. Verify the setup (optional)

1. `lgrep_status_semantic(path="/path/to/your/project")` — returns project stats or disk cache info.
2. `lgrep_search_semantic(query="authentication flow", path="/path/to/your/project")` — returns ranked results.

## Tool Selection Decision Matrix

| If the task is... | Best tool | Why |
|---|---|---|
| Intent/concept discovery | `lgrep_search_semantic` | Semantic retrieval finds meaning, not just text |
| Find a function/class by name | `lgrep_search_symbols` | Exact symbol lookup, no API key needed |
| Get all symbols in a file | `lgrep_get_file_outline` | Instant AST outline, no index needed |
| Get all symbols in a repo | `lgrep_get_repo_outline` | Full repo structure at a glance |
| Find exact text/identifier | `lgrep_search_text` or `Grep` | Literal text matching |
| Get symbol source code | `lgrep_get_symbol` | Byte-precise source retrieval |
| Known-file review | `Read` | Direct inspection avoids unnecessary search |

Examples:

- "Where do we enforce auth between route and service?" → `lgrep_search_semantic`
- "Find the `authenticate` function" → `lgrep_search_symbols`
- "What functions are in `src/auth.py`?" → `lgrep_get_file_outline`
- "Find references to `verifyToken`" → `lgrep_search_text` or `Grep`
- "Open `src/auth/jwt.ts` and explain it" → `Read`

## MCP Tools

### Semantic Engine (5 tools)

| Tool | Description |
|------|-------------|
| `lgrep_search_semantic(query, path, limit=10, hybrid=true)` | Search code by meaning. Returns file paths, line numbers, code snippets, and relevance scores. |
| `lgrep_index_semantic(path)` | Build or refresh the semantic index for a project directory. |
| `lgrep_status_semantic(path?)` | Check semantic index stats: file count, chunk count, watcher status. |
| `lgrep_watch_start_semantic(path)` | Start background file watcher for incremental re-indexing on save. |
| `lgrep_watch_stop_semantic(path?)` | Stop the background watcher. |

### Symbol Engine (11 tools)

| Tool | Description |
|------|-------------|
| `lgrep_index_symbols_folder(path, max_files=500, incremental=True)` | Index all symbols in a local folder. Run once before using symbol search. Incremental mode skips unchanged files (SHA-256). |
| `lgrep_index_symbols_repo(repo, ref="HEAD")` | Index symbols from a GitHub repo via REST API (no git clone). |
| `lgrep_list_repos()` | List all repositories that have been indexed in the symbol store. |
| `lgrep_get_file_tree(path, max_files=500)` | Get the file tree of a repository (respects .gitignore). |
| `lgrep_get_file_outline(path)` | Get the symbol outline (functions, classes, methods) for a single file. No index needed. |
| `lgrep_get_repo_outline(path, max_files=500)` | Get the symbol outline across an entire repository. |
| `lgrep_search_symbols(query, path, limit=20, kind?)` | Search for symbols by name (substring match). Requires prior indexing. |
| `lgrep_search_text(query, path, max_results=50)` | Literal text search across all source files. |
| `lgrep_get_symbol(symbol_id, path)` | Get full metadata and source code for a symbol by ID. |
| `lgrep_get_symbols(symbol_ids, path)` | Batch retrieval of multiple symbols by ID. |
| `lgrep_invalidate_cache(path)` | Remove the symbol index for a repository, forcing a full re-index. |

### Symbol IDs

Symbol IDs use the deterministic format `file_path:kind:name`:

```
src/auth.py:function:authenticate
src/auth.py:class:AuthManager
src/auth.py:method:login
```

IDs are stable across re-indexes (same file, kind, name → same ID). They break only on rename, which is the correct invalidation signal.

### Example: semantic search result

```json
{
  "results": [
    {
      "file_path": "src/auth/jwt.ts",
      "start_line": 42,
      "end_line": 68,
      "content": "export function verifyToken(token: string): Claims { ... }",
      "score": 0.92,
      "match_type": "hybrid"
    }
  ],
  "query_time_ms": 112,
  "total_chunks": 40000
}
```

### Example: symbol search result

```json
{
  "results": [
    {
      "id": "src/auth.py:function:authenticate",
      "name": "authenticate",
      "kind": "function",
      "file_path": "src/auth.py",
      "start_byte": 0,
      "end_byte": 120,
      "docstring": "Authenticate a user."
    }
  ],
  "total_matches": 1,
  "_meta": {
    "timing_ms": 0.4,
    "tokens_saved": 150,
    "session_tokens": 1350,
    "total_tokens": 48300,
    "cost_avoided_usd": 0.000145
  }
}
```

## Migration from v1.x

The 5 semantic tools were renamed in v2.0.0:

| v1.x name | v2.0.0 name |
|-----------|-------------|
| `lgrep_search` | `lgrep_search_semantic` |
| `lgrep_index` | `lgrep_index_semantic` |
| `lgrep_status` | `lgrep_status_semantic` |
| `lgrep_watch_start` | `lgrep_watch_start_semantic` |
| `lgrep_watch_stop` | `lgrep_watch_stop_semantic` |

All parameters and response shapes are unchanged. Update any hardcoded tool names in your prompts or scripts.

## Transport

lgrep uses `--transport streamable-http` as its standard deployment mode. One server process handles all OpenCode sessions concurrently — no per-session process spawning.

**Why not stdio?** When OpenCode connects to an MCP server via `type: local` (stdio), it spawns a new subprocess for each session. With lgrep's ~400MB footprint, 5 open sessions means ~2GB wasted on identical idle processes. The HTTP transport eliminates this entirely.

### Security

- **Localhost binding (default):** The server binds to `127.0.0.1`, preventing external network access. Do not change `--host` to `0.0.0.0` without a reverse proxy or firewall. This is a non-default, explicit opt-in.
- **No built-in authentication:** lgrep does not implement token-based auth on the HTTP transport. For multi-user or networked setups, place it behind a reverse proxy with authentication.
- **CORS / Origin:** lgrep does not set CORS headers. Browser-based MCP clients should not connect directly.

```bash
# Standard: local-only shared server
lgrep --transport streamable-http --host 127.0.0.1 --port 6285

# DANGER: Do NOT expose without auth/firewall:
# lgrep --transport streamable-http --host 0.0.0.0 --port 6285
```

## Configuration

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOYAGE_API_KEY` | For semantic engine | -- | Voyage AI API key |
| `LGREP_LOG_LEVEL` | No | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LGREP_CACHE_DIR` | No | `~/.cache/lgrep` | Where LanceDB vector databases are stored |
| `LGREP_WARM_PATHS` | No | -- | Colon-separated project paths to pre-load from disk cache at startup |

### Ignoring files

`lgrep` respects `.gitignore` automatically. For additional exclusions, generate a recommended `.lgrepignore` template:

```bash
lgrep init-ignore /path/to/project
```

Then customize it for your repo. Example entries:

```
# Skip large generated files
src/generated/
docs/site/
*.test.data
```

## Resource usage

| Resource | Idle | During indexing |
|----------|------|-----------------|
| **RAM** | ~300MB | ~500MB |
| **CPU** | <1% | <5% (Voyage does the heavy lifting) |
| **Disk** | ~250MB per semantic index + ~5MB per symbol index | -- |
| **Network** | Per semantic query (~90ms to Voyage) | Batch API calls during semantic index |
| **Cost** | -- | **~$3/month** for active usage |

## Supported languages

**Semantic engine:** AST-aware chunking via tree-sitter for 30+ languages including Python, TypeScript, JavaScript, Rust, Go, Ruby, Java, C/C++, C#, PHP, Swift, Kotlin, Scala, Lua, R, Julia, Elixir, Erlang, Haskell, OCaml, Bash, SQL, and more. Falls back to text chunking for unsupported file types.

**Symbol engine:** tree-sitter-language-pack with 165+ languages. Extracts functions, classes, methods, and interfaces.

## Troubleshooting

**"VOYAGE_API_KEY not set"** — If using Vision (e.g. via open-chad), add the key to `~/.config/vision/servers.yaml` under `lgrep.env.VOYAGE_API_KEY`. If using OpenCode directly, add it to the `env` section of your OpenCode MCP config. The symbol engine works completely without this key.

**Slow first semantic index** — Initial indexing embeds every file. Subsequent runs skip unchanged files via SHA-256 hashing. A ~8k file project takes ~15-20 minutes on first index.

**"Failed to initialize project"** — Check that the path exists and is a directory. Check server logs (`LGREP_LOG_LEVEL=DEBUG`) for details.

**Dependency build failures** — `lgrep` depends on native extensions (LanceDB, tree-sitter). On most platforms, prebuilt wheels are available. If not, ensure you have a C compiler and Rust toolchain installed.

**Stale semantic results** — Call `lgrep_index_semantic` to refresh, or use `lgrep_watch_start_semantic` for automatic incremental updates.

**Symbol search returns "not indexed"** — Run `lgrep_index_symbols_folder(path=...)` first to build the symbol index.

## Development

```bash
git clone https://github.com/Sharper-Flow/lgrep.git
cd lgrep
pip install -e ".[dev]"
pytest -v
```

410+ tests covering all modules: embeddings, storage, chunking, discovery, indexing, watcher, server tools, auto-index, concurrency, CLI transport, installer safety, symbol parser, symbol storage, symbol tools, and integration.

## License

MIT -- see [LICENSE](LICENSE).
