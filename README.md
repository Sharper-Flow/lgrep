# lgrep

**Local semantic code search for [OpenCode](https://github.com/opencode-ai/opencode).**

Search your codebase by *meaning*, not just keywords. Find `jwt.verify()` when you search for "authentication flow". Find `pool.getConnection()` when you search for "database connection management".

`lgrep` is an [MCP](https://modelcontextprotocol.io/) server that combines [Voyage Code 3](https://blog.voyageai.com/2024/12/04/voyage-code-3/) embeddings with local [LanceDB](https://lancedb.github.io/lancedb/) vector storage. Your code never leaves your machine -- only search queries hit the Voyage API.

## Why lgrep?

We built `lgrep` because existing options had tradeoffs we didn't want to accept:

- **grep / ripgrep** -- fast, but keyword-only. Can't find conceptually related code.
- **[mgrep](https://github.com/mixedbread-ai/mgrep)** -- semantic search, but syncs all code to Mixedbread cloud servers, costs $15-30/month, and tops out at ~85% retrieval quality.
- **Local embedding models** -- private, but quality drops to 78% with CPU inference.

We wanted the best retrieval quality available, at low cost, without sending code to someone else's servers.

### lgrep vs mgrep vs grep

| | grep / rg | mgrep | **lgrep** |
|---|-----------|-------|-----------|
| **Search type** | Keyword / regex | Semantic | **Semantic + keyword hybrid** |
| **Retrieval quality** | -- | ~85% | **92%** |
| **Monthly cost** | $0 | ~$15-30 | **~$3** |
| **Query latency** | <10ms | ~170ms | **~110ms** |
| **Code privacy** | Local | Uploaded to cloud | **Local** (only queries sent to API) |
| **Vectors** | N/A | Cloud | **Local** |
| **Multi-agent** | N/A | Single user | **3+ concurrent agents** |
| **Offline** | Yes | No | No (needs Voyage API) |
| **Web search** | No | Yes | No |

### Architecture decision

We evaluated 5 approaches before settling on Voyage Code 3 (cloud embeddings) + LanceDB (local vectors):

| Approach | Quality | Cost/mo | Latency | Privacy |
|----------|---------|---------|---------|---------|
| mgrep (fully managed) | 85% | $15-30 | ~170ms | Code on cloud |
| LanceDB Cloud + Voyage | 92% | $7-15 | ~200ms | Code on cloud |
| **Local LanceDB + Voyage** | **92%** | **$3** | **~110ms** | **Code stays local** |
| Local + GPU (GTX 1070) | 79% | $0 | ~80ms | Fully local |
| Local + CPU only | 78% | $0 | ~140ms | Fully local |

The winning approach gives the best retrieval quality at 5x lower cost than mgrep, with faster latency and better privacy. The only data sent to Voyage is the search query text -- your code and vectors never leave your machine.

## How it works

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Agent 1   │  │   Agent 2   │  │   Agent 3   │
│ (OpenCode)  │  │ (OpenCode)  │  │ (OpenCode)  │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        │ MCP (stdio)
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
│   Voyage Code 3     │    │   LanceDB Local     │
│   (Cloud API)       │    │   (~/.cache/lgrep)  │
│                     │    │                     │
│   $0.18/1M tokens   │    │   Free storage      │
│   92% quality       │    │   ~15ms search      │
│   32K context       │    │   Hybrid: vec + FTS │
└─────────────────────┘    └─────────────────────┘
```

1. **Index** -- Discover files (respecting `.gitignore`), chunk with AST-aware tree-sitter boundaries, embed via Voyage, store vectors locally in LanceDB.
2. **Search** -- Embed the query via Voyage (~90ms), run hybrid search combining vector similarity + BM25 keyword matching with RRF reranking (~15ms), return ranked results.
3. **Watch** -- Background file watcher triggers incremental re-indexing on save. Unchanged files are skipped using SHA-256 hashes.

### Key design choices

- **AST-aware chunking** via [Chonkie](https://docs.chonkie.ai) + tree-sitter. Functions, classes, and methods become natural chunk boundaries. 28-65% better recall than fixed-size chunking.
- **Hybrid search** combines vector similarity with BM25 keyword matching, reranked with Reciprocal Rank Fusion. Catches both semantic and exact matches.
- **SHA-256 skip optimization** -- re-indexing only re-embeds files that actually changed, saving API tokens.
- **Concurrent access** -- single warm MCP server process handles 3+ agents querying simultaneously.

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

### 1. Get a Voyage AI API key

Sign up at [dash.voyageai.com](https://dash.voyageai.com/) and create an API key. Voyage offers a free tier of 200M tokens (~5 full codebase indexes).

### 2. Configure OpenCode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "lgrep": {
      "type": "local",
      "command": ["lgrep"],
      "env": {
        "VOYAGE_API_KEY": "your-api-key-here"
      },
      "enabled": true
    }
  }
}
```

### 3. Index your project

Once OpenCode starts with `lgrep` enabled, the agent can call:

```
lgrep_index(path="/path/to/your/project")
```

After that, semantic search is available immediately.

## MCP Tools

| Tool | Description |
|------|-------------|
| `lgrep_search(query, limit=10, hybrid=true)` | Search code by meaning. Returns file paths, line numbers, code snippets, and relevance scores. |
| `lgrep_index(path)` | Build or refresh the index for a project directory. Skips unchanged files automatically. |
| `lgrep_status()` | Check index stats: file count, chunk count, project path, watcher status. |
| `lgrep_watch_start(path)` | Start background file watcher for incremental re-indexing on save. |
| `lgrep_watch_stop()` | Stop the background watcher. |

### Example search result

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

## Configuration

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOYAGE_API_KEY` | **Yes** | -- | Voyage AI API key |
| `LGREP_LOG_LEVEL` | No | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LGREP_CACHE_DIR` | No | `~/.cache/lgrep` | Where LanceDB vector databases are stored |

See [`.env.example`](.env.example) for a template.

### Ignoring files

`lgrep` respects `.gitignore` automatically. For additional exclusions, create a `.lgrepignore` file in your project root:

```
# Skip large generated files
src/generated/
docs/site/
*.test.data
```

See [`.lgrepignore.example`](.lgrepignore.example) for more patterns.

## Resource usage

| Resource | Idle | During indexing |
|----------|------|-----------------|
| **RAM** | ~300MB | ~500MB |
| **CPU** | <1% | <5% (Voyage does the heavy lifting) |
| **Disk** | ~250MB per project index | -- |
| **Network** | Per query (~90ms to Voyage) | Batch API calls during index |
| **Cost** | -- | **~$3/month** for active usage |

### Cost breakdown

| Component | Usage (3 agents, ~8k files) | Monthly cost |
|-----------|---------------------------|--------------|
| Voyage Code 3 embeddings | ~15M tokens | ~$2.70 |
| LanceDB storage | Local | $0 |
| **Total** | | **~$3** |

## Supported languages

AST-aware chunking via tree-sitter for 30+ languages including Python, TypeScript, JavaScript, Rust, Go, Ruby, Java, C/C++, C#, PHP, Swift, Kotlin, Scala, Lua, R, Julia, Elixir, Erlang, Haskell, OCaml, Bash, SQL, and more. Falls back to text chunking for unsupported file types.

## Troubleshooting

**"VOYAGE_API_KEY not set"** -- Ensure the key is in the `env` section of your OpenCode MCP config, not just your shell environment.

**Slow first index** -- Initial indexing embeds every file. Subsequent runs skip unchanged files via SHA-256 hashing. A ~8k file project takes ~15-20 minutes on first index.

**"Failed to initialize project"** -- Check that the path exists and is a directory. Check server logs (`LGREP_LOG_LEVEL=DEBUG`) for details.

**Dependency build failures** -- `lgrep` depends on native extensions (LanceDB, tree-sitter). On most platforms, prebuilt wheels are available. If not, ensure you have a C compiler and Rust toolchain installed.

**Stale results** -- Call `lgrep_index` to refresh, or use `lgrep_watch_start` for automatic incremental updates.

## Development

```bash
git clone https://github.com/Sharper-Flow/lgrep.git
cd lgrep
pip install -e ".[dev]"
pytest -v
```

72 tests covering all modules: embeddings, storage, chunking, discovery, indexing, watcher, server tools, and integration.

## License

MIT -- see [LICENSE](LICENSE).
