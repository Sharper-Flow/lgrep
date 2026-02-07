# lgrep

**Local semantic code search for [OpenCode](https://github.com/opencode-ai/opencode).**

Search your codebase by *meaning*, not just keywords. Find `jwt.verify()` when you search for "authentication flow". Find `pool.getConnection()` when you search for "database connection management".

`lgrep` is an [MCP](https://modelcontextprotocol.io/) server that combines [Voyage Code 3](https://blog.voyageai.com/2024/12/04/voyage-code-3/) embeddings with local [LanceDB](https://lancedb.github.io/lancedb/) vector storage. Your code never leaves your machine -- only search queries hit the Voyage API.

## Inspiration

`grep` is one of the most important tools ever built for developers. Forty years later, we still reach for it (or ripgrep) dozens of times a day. But grep searches text, not meaning. When you're working with AI agents that think in concepts -- "find the authentication flow", "where is rate limiting handled" -- keyword matching falls short.

Semantic code search exists, but every option we found required either uploading your code to someone else's servers, paying a recurring subscription, or accepting significantly worse search quality with local models. We wanted something different: a first-class grep replacement that understands code semantically, keeps everything local, and doesn't cost $20/month to run.

`lgrep` is our answer. It's built for developers and AI agents who want the best available search quality without a cloud dependency for their codebase. Your vectors live on your machine. Your code never leaves your disk. The only thing that touches the network is the short natural language query you're searching for.

We also built it because we run multiple AI coding agents simultaneously and needed a shared semantic index they could all query without stepping on each other. Nothing we found supported that well.

## Why lgrep?

The existing options all have tradeoffs we didn't want to accept:

- **grep / ripgrep** -- fast and local, but keyword-only. Can't find conceptually related code. Searching for "error handling in API routes" returns nothing if the code uses `try/catch` with `res.status(500)`.
- **[mgrep](https://github.com/mixedbread-ai/mgrep)** -- semantic search that works, but uploads all your code to Mixedbread's cloud servers, costs $15-30/month, and uses embeddings that top out at ~85% retrieval quality.
- **Local embedding models** -- fully private, but quality drops to 78% with CPU inference. The gap between 78% and 92% is the difference between useful results and noise.

We wanted the best retrieval quality available, at low cost, without handing our codebase to a third party.

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
| **Setup effort** | None | Minimal | Moderate (API key + config) |
| **Maturity** | Decades | Production | v0.1.0 |

### Performance

| Metric | mgrep | lgrep | Notes |
|--------|-------|-------|-------|
| **Total query latency** | ~170ms | ~110ms | lgrep benefits from local vector search |
| **Embedding time** | Included in query | ~90ms | Both require a network call to an embedding API |
| **Search time** | Unknown (cloud) | ~15ms | LanceDB local search is fast |
| **Initial index (~8k files)** | Unknown | ~15-20 min | Bottlenecked by embedding API rate limits |
| **Incremental update** | Unknown | <2s per file | Hash-based skip avoids re-embedding unchanged files |
| **RAM (idle)** | Unknown | ~300MB | Python + LanceDB |
| **RAM (indexing)** | Unknown | ~500MB | Batch processing |
| **Disk per project** | Cloud-hosted | ~250MB | Local LanceDB index |

### Where mgrep wins

Being objective: mgrep is the better choice if you value any of the following.

- **Zero setup friction.** Install and go. No API key signup, no environment variables, no MCP config. lgrep requires a Voyage AI account, an API key, and OpenCode configuration.
- **Web search.** mgrep can search the web alongside your codebase. lgrep is code-only.
- **Multimodal support.** mgrep handles more than just code. lgrep is purpose-built for code search.
- **Maturity.** mgrep is a production tool maintained by Mixedbread. lgrep is v0.1.0.

### Where lgrep wins

- **Retrieval quality.** Voyage Code 3 scores 92% on code retrieval benchmarks vs Mixedbread's ~85%. In practice this means fewer missed results and better ranking for conceptual queries.
- **Cost.** ~$3/month vs $15-30/month. 5-10x cheaper.
- **Privacy.** Your code and vectors never leave your machine. Only the search query text (a short natural language string) is sent to the Voyage API. mgrep uploads all code to Mixedbread's cloud.
- **Hybrid search.** lgrep combines vector similarity with BM25 keyword matching using Reciprocal Rank Fusion. This catches both semantic matches ("authentication flow" → `jwt.verify()`) and exact keyword matches that pure vector search can miss.
- **Multi-agent.** A single lgrep server handles 3+ concurrent OpenCode agents querying the same index. Designed for multi-agent workflows from the start.

### Caveats on these numbers

We want to be transparent about what we know and don't know:

- **Quality percentages** (92% vs 85%) come from published embedding benchmarks ([Voyage Code 3](https://blog.voyageai.com/2024/12/04/voyage-code-3/), [DataStax 2025 benchmark](https://dev.to/datastax/the-best-embedding-models-for-information-retrieval-in-2025-3dp5)), not our own head-to-head retrieval tests on the same codebase. Real-world results may differ.
- **mgrep latency** (~170ms) is estimated from typical cloud API round-trip overhead. We haven't profiled mgrep directly. mgrep may have internal optimizations (caching, precomputed results) that improve this.
- **mgrep cost** ($15-30/month) is based on published pricing at time of development. Check current pricing.
- **lgrep latency** (~110ms) is measured: ~90ms Voyage API call + ~15ms local LanceDB search + overhead. Actual latency varies with network conditions and index size.

### Embedding models compared

lgrep uses Voyage Code 3 by default. Here's how it compares to alternatives:

| Provider | Model | Code retrieval quality | Cost per 1M tokens | Context window |
|----------|-------|----------------------|---------------------|----------------|
| **Voyage (default)** | voyage-code-3 | 92% | $0.18 | 32K |
| OpenAI | text-embedding-3-large | ~85% | $0.13 | 8K |
| Mixedbread (mgrep) | mixedbread-ai | ~85% | Included in subscription | Unknown |
| Local CPU (Jina) | jina-code-v2 | ~78% | $0 | 8K |
| Local GPU (GTX 1070) | jina-code-v2 | ~79% | $0 (hardware cost) | 8K |

Voyage Code 3's 32K context window is particularly relevant for code -- it allows larger chunks that preserve more structural context than models limited to 8K tokens.

### Architecture decision

We evaluated 5 approaches before settling on Voyage Code 3 (cloud embeddings) + LanceDB (local vectors):

| Approach | Quality | Cost/mo | Latency | Privacy |
|----------|---------|---------|---------|---------|
| mgrep (fully managed) | 85% | $15-30 | ~170ms | Code on cloud |
| LanceDB Cloud + Voyage | 92% | $7-15 | ~200ms | Code on cloud |
| **Local LanceDB + Voyage** | **92%** | **$3** | **~110ms** | **Code stays local** |
| Local + GPU (GTX 1070) | 79% | $0 | ~80ms | Fully local |
| Local + CPU only | 78% | $0 | ~140ms | Fully local |

The fully local options (GPU/CPU) are the cheapest and most private, but the quality drop from 92% to 78-79% is significant -- it means noticeably worse search results. The cloud-managed options (mgrep, LanceDB Cloud) sacrifice privacy. Voyage + local LanceDB hits the best balance: top-tier quality, low cost, code stays on your machine.

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
| `lgrep_search(query, path, limit=10, hybrid=true)` | Search code by meaning. `path` (required) selects which project to search. Returns file paths, line numbers, code snippets, and relevance scores. |
| `lgrep_index(path)` | Build or refresh the index for a project directory. Skips unchanged files automatically. |
| `lgrep_status(path?)` | Check index stats: file count, chunk count, watcher status. Omit `path` for all projects. |
| `lgrep_watch_start(path)` | Start background file watcher for incremental re-indexing on save. |
| `lgrep_watch_stop(path?)` | Stop the background watcher. Omit `path` to stop all watchers. |

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

81 tests covering all modules: embeddings, storage, chunking, discovery, indexing, watcher, server tools, and integration.

## License

MIT -- see [LICENSE](LICENSE).
