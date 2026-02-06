# lgrep: Local Semantic Code Search MCP Server for OpenCode

## Summary

Build `lgrep`, a high-quality semantic code search **MCP server** that combines **Voyage Code 3** embeddings (92% retrieval quality) with **local LanceDB** vector storage. Designed for **concurrent access by 3+ AI agents** with sub-200ms query latency.

**Architecture**: Cloud embeddings (Voyage) + Local search (LanceDB)

**Key differentiators vs mgrep**:
| Metric | mgrep | lgrep |
|--------|-------|-------|
| Retrieval quality | ~85% | **92%** (Voyage Code 3) |
| Monthly cost | ~$15-30 | **~$3** |
| Query latency | ~170ms | **~155ms** |
| Code sync | To cloud | **Never leaves machine** |

## Motivation

### Problem
- **mgrep syncs code to cloud** - all code uploaded to Mixedbread servers
- **mgrep quality ceiling** - Mixedbread embeddings ~85% vs Voyage Code 3's 92%
- **mgrep cost** - ~$15-30/month for active usage
- **grep/ripgrep are keyword-only** - can't find "authentication flow" when code says `jwt.verify()`
- **Multiple agents need concurrent access** - need shared MCP server model

### User Context
- Two large projects: `~/dev/pokeedge` (~7k files), `~/dev/pokeedge-web` (~8k files)
- **3 concurrent AI coding agents** sharing the index
- OpenCode TUI with MCP support
- Hardware: 64GB RAM (plenty of headroom)
- Need: Best quality semantic search at low cost

### Why This Architecture (Local + Voyage)

| Decision | Rationale |
|----------|-----------|
| **Voyage Code 3 embeddings** | 92% retrieval quality - best available for code |
| **LanceDB local storage** | ~15ms search, $0 storage cost, vectors never leave machine |
| **MCP server model** | Single process serves 3+ agents, no cold starts |
| **Python** | LanceDB Python has complete API; MCP server stays warm |

### Cost Analysis

| Component | Usage (3 agents, pokeedge-web) | Monthly Cost |
|-----------|-------------------------------|--------------|
| Voyage Code 3 embeddings | ~15M tokens/month | ~$2.70 |
| LanceDB storage | Local | $0 |
| **Total** | | **~$3/month** |

vs mgrep: **5x cheaper** with **+7% better quality**

## Design

### Architecture Overview

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
│   (Cloud API)       │    │   (~/.cache/lgrep/) │
│                     │    │                     │
│   $0.18/1M tokens   │    │   FREE storage      │
│   92% quality       │    │   ~15ms search      │
│   32K context       │    │   Hybrid: vec+FTS   │
└─────────────────────┘    └─────────────────────┘
```

### Data Flow

1. **Query arrives** via MCP from any agent
2. **Embed query** via Voyage Code 3 API (~90ms)
3. **Search vectors** in local LanceDB (~15ms)
4. **Hybrid rerank** with BM25 keyword matching (~5ms)
5. **Return results** with file, line, content, score

**Total query latency: ~110-155ms** (vs mgrep's ~170ms)

### Components

#### 1. lgrep MCP Server (Python)

**MCP Tools exposed:**

| Tool | Parameters | Returns |
|------|------------|---------|
| `lgrep_search` | `query: str, limit: int = 10, hybrid: bool = true` | `SearchResults` |
| `lgrep_index` | `path: str` | `IndexStatus` |
| `lgrep_status` | - | `{files, chunks, last_updated, watching}` |
| `lgrep_watch_start` | `path: str` | `{watching: bool}` |
| `lgrep_watch_stop` | - | `{stopped: bool}` |

**Search result format:**
```json
{
  "results": [
    {
      "file": "src/auth/jwt.ts",
      "line": 42,
      "content": "export function verifyToken(token: string): Claims {",
      "score": 0.92,
      "match_type": "hybrid"
    }
  ],
  "query_time_ms": 125,
  "embed_time_ms": 90,
  "search_time_ms": 35,
  "total_chunks": 40000
}
```

#### 2. Voyage Code 3 Integration

| Aspect | Value |
|--------|-------|
| **Model** | `voyage-code-3` |
| **Dimensions** | 1024 (default, Matryoshka: 256-2048) |
| **Context** | 32K tokens |
| **Quality** | 92% on code retrieval benchmarks |
| **Pricing** | $0.18 per 1M tokens |
| **Free tier** | 200M tokens (~5 full codebase indexes) |

**API Integration:**
```python
import voyageai

client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])

def embed(texts: list[str]) -> list[list[float]]:
    result = client.embed(texts, model="voyage-code-3", input_type="document")
    return result.embeddings

def embed_query(query: str) -> list[float]:
    result = client.embed([query], model="voyage-code-3", input_type="query")
    return result.embeddings[0]
```

#### 3. LanceDB Local Storage

- **Location**: `~/.cache/lgrep/<sha256(project-path)[:12]>/`
- **Hybrid search**: Vector similarity + BM25 via native FTS
- **Concurrent reads**: Single connection handles all agents

**Schema:**
```python
import lancedb
from lancedb.pydantic import LanceModel, Vector

class CodeChunk(LanceModel):
    id: str                          # uuid
    file_path: str                   # relative to project root
    chunk_index: int                 # position in file
    start_line: int
    end_line: int
    content: str                     # chunk text
    embedding: Vector(1024)          # Voyage Code 3 dimensions
    file_hash: str                   # for invalidation
    indexed_at: float                # timestamp
```

#### 4. File Watcher (integrated)

- Uses `watchdog` for cross-platform file monitoring
- Runs as async task within MCP server process
- Incremental updates: only re-embed changed files
- Respects .gitignore and .lgrepignore
- Debounces rapid changes (100ms)
- Batches changes to minimize Voyage API calls

#### 5. OpenCode Integration

**MCP Configuration:**
```json
// ~/.config/opencode/opencode.json
{
  "mcp": {
    "lgrep": {
      "type": "local",
      "command": ["python", "-m", "lgrep.server"],
      "env": {
        "VOYAGE_API_KEY": "${VOYAGE_API_KEY}"
      },
      "enabled": true
    }
  }
}
```

**Agent Skill (optional):**
```
plugins/lgrep/
└── skills/lgrep/
    └── SKILL.md    # "Use lgrep_search for semantic code queries"
```

### Chunking Strategy

**Research-informed settings:**
- **Strategy**: AST-aware via tree-sitter when possible, fixed-size fallback
- **Size**: 400-500 tokens per chunk (within Voyage's 32K context)
- **Overlap**: 100-150 tokens (25-30% - research shows optimal)
- **Metadata**: Preserve file path, line numbers, language
- **Contextualization**: Prepend file path + scope chain to chunk

**AST chunking** (28% better Recall@5 than fixed-size):
- Functions, classes, methods as natural boundaries
- Falls back to fixed-size for very large functions or unsupported languages

### Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| **Query latency (warm)** | <200ms | 90ms embed + 15ms search + overhead |
| **Query latency (p99)** | <300ms | Network variance |
| **Search-only latency** | <20ms | Local LanceDB |
| **Initial index (40k chunks)** | <20 min | Voyage API rate limited |
| **Incremental update** | <2s | Single file re-embed |
| **Memory (server idle)** | <300MB | No local model |
| **Memory (during index)** | <500MB | Batch processing |
| **Concurrent queries** | 3+ | MCP handles queuing |

### System Resource Usage

| Resource | Usage | Notes |
|----------|-------|-------|
| **RAM (idle)** | ~300MB | Python + LanceDB |
| **RAM (peak)** | ~500MB | During batch indexing |
| **CPU (idle)** | <1% | Watching for changes |
| **CPU (query)** | <5% | Minimal - Voyage does embedding |
| **Disk** | ~250MB | LanceDB index per project |
| **Network** | Per query | ~90ms to Voyage API |

## Implementation Status

### Progress Overview

| Category | Done | Cancelled | Pending | Total |
|----------|------|-----------|---------|-------|
| Setup    | 1    | 2         | 0       | 3     |
| Core     | 12   | 6         | 0       | 18    |
| MCP      | 5    | 0         | 0       | 5     |
| Plugin   | 2    | 4         | 0       | 6     |
| Testing  | 4    | 0         | 2       | 6     |
| Docs     | 3    | 0         | 0       | 3     |
| Research | 0    | 3         | 0       | 3     |
| Hardening| 0    | 0         | 8       | 8     |
| **Total**| **27** | **15** | **10**  | **52** |

### Architecture Evolution

The original proposal called for a Rust + Cargo workspace with Jina Code V2 local embeddings.
During research, the architecture pivoted to:

1. **Python MCP server** (LanceDB Python has complete hybrid search API; Rust API is incomplete)
2. **Voyage Code 3** cloud embeddings (92% quality vs 78% local; ~$3/mo cost)
3. **Chonkie CodeChunker** for AST-aware chunking (28-65% recall improvement over fixed-size)
4. **FastMCP** decorator-based server (auto-generates tool schemas from type hints)

All cancelled tasks reflect this architectural pivot - the replacement tasks are complete.

### Gate Status

| Gate | Status | Completed |
|------|--------|-----------|
| Research | Done | 2026-02-05 |
| Prep | Done | 2026-02-05 |
| Implementation | Done | 2026-02-05 |
| Review | **Pending** | - |
| Harden | **Pending** | - |
| Signoff | **Pending** | - |

## Hardening Plan

The following gaps were identified during `/adv-prep` gap analysis and must be addressed before the review and harden gates.

### MUST Fix (Blocking - Security/Correctness)

#### H1: SQL Injection in ChunkStore.delete_by_file
- **File**: `src/lgrep/storage.py:182`
- **Issue**: `file_path` is interpolated into an f-string SQL predicate. File paths containing single quotes will break the query or enable injection.
- **Fix**: Use parameterized deletion or escape single quotes.
- **Task**: `tk-O3xfwJMl`

#### H2: Blocking time.sleep() in VoyageEmbedder Retry Loops
- **File**: `src/lgrep/embeddings.py:115,164`
- **Issue**: `time.sleep()` in retry loops blocks the entire event loop, breaking concurrent query support (3+ agents).
- **Fix**: Convert to `asyncio.sleep()` with async methods, or run embedding calls in an executor.
- **Task**: `tk-_2TZ5uOd`

#### H3: CLI Stub Dead Code
- **File**: `src/lgrep/cli.py`
- **Issue**: `index` and `status` CLI commands print "not yet implemented" - dead code that confuses users.
- **Fix**: Remove the stubs (MCP server is the primary interface) or wire them to Indexer/ChunkStore.
- **Task**: `tk-ATcBqIzC`

#### H4: Token Cost Threshold Warnings Incomplete
- **Issue**: Task tk-mrH2LkSq claimed $5/$10 warnings but only a cumulative counter exists. No cost calculation, no threshold comparison, no warnings.
- **Fix**: Add Voyage Code 3 pricing calculation ($0.18/1M tokens), threshold comparison, and structured log warnings. Optionally persist token counts across restarts.
- **Task**: `tk-j3EreIsw`

### SHOULD Fix (Important - Reliability)

#### H5: LanceDB Corruption Recovery
- **Issue**: No graceful handling if LanceDB index becomes corrupted. Server crashes.
- **Fix**: Wrap ChunkStore init and table property with try/except; detect corruption, log warning, offer rebuild.
- **Task**: `tk-X5glc5Rz`

#### H6: Watcher File Extension Filter
- **Issue**: `IndexingHandler` triggers re-index on ANY file change (images, binaries, .lock files), wasting Voyage API tokens.
- **Fix**: Check against `LANGUAGE_MAP` extensions before scheduling indexing.
- **Task**: `tk-dLNQyU6h`

#### H7: VoyageEmbedder Retry/Backoff Tests
- **Issue**: No tests for retry behavior, exponential backoff, or permanent failure after MAX_RETRIES.
- **Fix**: Add unit tests with mocked transient failures.
- **Task**: `tk-urAZFDPJ`

#### H8: Server Tool Error Path Tests
- **Issue**: No tests for error paths: invalid path, missing API key, indexing failure, search with no index.
- **Fix**: Add test cases for all error paths in server tools.
- **Task**: `tk-UDq50edy`

### COULD Fix (Nice to Have - Performance/Polish)

#### H9: get_indexed_files Memory Optimization
- **Issue**: Loads entire table into memory via `to_arrow()` to get file paths. For 75k chunks this is wasteful.
- **Fix**: Use `SELECT DISTINCT` query or LanceDB filter to get unique `file_path` values only.
- **Task**: `tk-g4aND_HO`

#### H10: PEP 561 py.typed Marker
- **Issue**: Missing `py.typed` marker file for type checking support.
- **Fix**: Add `py.typed` file to `src/lgrep/`.
- **Task**: `tk-5_THeAWk`

### Cross-Cutting Concerns Assessment

| Concern | Status | Notes |
|---------|--------|-------|
| Error Handling | Gaps: H1, H5 | SQL injection, DB corruption |
| Logging | Addressed | structlog JSON throughout |
| Validation | Addressed | Path validation, API key checks |
| Security | Gap: H1 | SQL injection in delete_by_file |
| Performance | Gaps: H2, H9 | Blocking sleep, memory in get_indexed_files |
| Caching | N/A | LanceDB is the cache |
| Config | Addressed | Env vars for API key, log level, cache dir |
| Monitoring | Gap: H4 | Cost threshold warnings incomplete |
| Persistence | Addressed | LanceDB handles data persistence |
| Concurrency | Gap: H2 | sleep() blocks event loop during retry |
| i18n/L10n | N/A | Developer tool, English only |
| Privacy | N/A | All data local, API key via env var |

## Acceptance Criteria

### Core Functionality
- [ ] `lgrep_search("query")` returns semantically relevant results
- [ ] Hybrid search combines vector similarity + keyword matching
- [ ] Results include file path, line number, and context snippet
- [ ] Query latency <200ms for 40k chunk index (warm server)
- [ ] 92% retrieval quality (Voyage Code 3 benchmark)

### Indexing
- [ ] `lgrep_index(path)` builds full index for specified directory
- [ ] Respects .gitignore (and .lgrepignore if present)
- [ ] Incremental updates for changed files only
- [ ] Index persists across sessions in ~/.cache/lgrep/
- [ ] Batches Voyage API calls for efficiency

### Background Watcher
- [ ] `lgrep_watch_start(path)` starts monitoring for file changes
- [ ] Watcher updates index incrementally on file save
- [ ] Debounces rapid changes (100ms)
- [ ] Clean shutdown via `lgrep_watch_stop()`

### Concurrent Access
- [ ] 3 agents can query simultaneously without contention
- [ ] Voyage API calls are serialized to respect rate limits
- [ ] No LanceDB locking issues with concurrent reads

### OpenCode Integration
- [ ] MCP server starts via opencode.json configuration
- [ ] VOYAGE_API_KEY passed via environment
- [ ] skills/SKILL.md guides agent to use lgrep for semantic queries
- [ ] Works with Vision MCP manager (optional)

### Quality
- [ ] Semantic search finds "authentication flow" when code uses `jwt.verify()`
- [ ] Vectors stored locally (only query text sent to Voyage)
- [ ] Tests cover core search and indexing functionality

### Hardening
- [ ] No SQL injection in file path handling (parameterized queries)
- [ ] Retry loops use async sleep (event loop not blocked)
- [ ] No dead CLI stub code (removed or implemented)
- [ ] Token cost warnings fire at $5/$10 thresholds
- [ ] Graceful LanceDB corruption recovery (no server crash)
- [ ] Watcher ignores non-code files (no token waste on binaries)
- [ ] Retry/backoff behavior tested (transient + permanent failure)
- [ ] Server tool error paths tested (invalid path, missing key, no index)
- [ ] get_indexed_files uses efficient query (no full table load)
- [ ] py.typed marker present for PEP 561

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VOYAGE_API_KEY` | Yes | Voyage AI API key |
| `LGREP_CACHE_DIR` | No | Override default ~/.cache/lgrep/ |
| `LGREP_LOG_LEVEL` | No | DEBUG, INFO, WARNING, ERROR |

### Config File (optional)

```yaml
# ~/.config/lgrep/config.yaml
embedding:
  provider: voyage
  model: voyage-code-3
  dimensions: 1024  # or 512 for 2x storage savings

chunking:
  max_tokens: 500
  overlap_tokens: 100
  use_ast: true  # tree-sitter AST chunking

index:
  cache_dir: ~/.cache/lgrep
  ignore_patterns:
    - "*.min.js"
    - "node_modules"
    - ".git"
```

## Out of Scope (v1)

- Local embedding fallback (CodeRankEmbed for offline)
- GPU acceleration
- GUI/TUI interface (MCP only)
- Multi-model selection at runtime
- Remote/distributed index
- IDE integrations (VS Code, etc.)

## Technical Risks

| Risk | Mitigation | Status |
|------|------------|--------|
| Voyage API rate limits | Batch embedding, queue during index | Implemented |
| Voyage API latency spikes | Timeout + retry with backoff | Implemented (but uses blocking sleep - H2) |
| Voyage API cost overrun | Track token usage, warn at thresholds | Partial (counter exists, warnings missing - H4) |
| LanceDB corruption | Graceful recovery + rebuild option | **Not yet implemented** (H5) |
| MCP server crashes | Graceful error handling, auto-restart | Partial (happy paths only - H8) |
| SQL injection via file paths | Parameterized deletion | **Not yet implemented** (H1) |
| Event loop blocking | Async retry loops | **Not yet implemented** (H2) |
| Token waste on non-code files | Extension filter in watcher | **Not yet implemented** (H6) |

## Research Validation

### Architecture Decision Record

**Decision**: Use Voyage Code 3 (cloud) + LanceDB (local) instead of fully local embeddings.

**Context**: Evaluated 5 options ranging from fully managed (mgrep) to fully local (CPU embeddings).

**Options Considered**:
| Option | Quality | Cost/mo | Latency |
|--------|---------|---------|---------|
| mgrep | 85% | $15-30 | ~170ms |
| LanceDB Cloud + Voyage | 92% | $7-15 | ~200ms |
| **Local + Voyage** | **92%** | **$3** | **~155ms** |
| Local + GPU (1070) | 79% | $0 | ~80ms |
| Local + CPU | 78% | $0 | ~140ms |

**Decision**: Option 3 (Local + Voyage) - best quality-to-cost ratio.

**Rationale**:
- 92% retrieval quality (best available)
- ~$3/month (5x cheaper than mgrep)
- ~155ms latency (faster than mgrep)
- Vectors stay local (only query text sent to API)
- Single vendor dependency (easy to swap to OpenAI/Cohere)

### Wisdom Accumulated
- **PATTERN**: MCP server model for 3+ concurrent agents - single warm process, shared state
- **PATTERN**: Hybrid search requires explicit reranker (RRF)
- **PATTERN**: AST chunking via tree-sitter gives 28% better recall
- **PATTERN**: 100-150 token overlap (25-30%) is optimal for code chunks
- **GOTCHA**: LanceDB Rust API is incomplete - use Python
- **CONVENTION**: Python for MCP server - complete LanceDB API, no startup overhead

## Comparison vs mgrep

| Aspect | mgrep | lgrep |
|--------|-------|-------|
| **Retrieval quality** | ~85% | **92%** |
| **Monthly cost** | ~$15-30 | **~$3** |
| **Query latency** | ~170ms | **~155ms** |
| **Code storage** | Mixedbread cloud | **Local only** |
| **Vectors** | Cloud | **Local** |
| **Vendor lock-in** | Mixedbread | Voyage (swappable) |
| **Multimodal** | Yes | No (code only) |
| **Web search** | Yes | No |
| **Build effort** | Zero | Medium |

## Dependencies

```toml
# pyproject.toml
[project]
name = "lgrep"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "lancedb>=0.5.0",
    "voyageai>=0.3.0",
    "mcp>=1.0.0",
    "watchdog>=4.0.0",
    "chonkie[code]>=1.5.0",       # AST-aware chunking (wraps tree-sitter)
    "gitignorefile>=1.1.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
]

[project.optional-dependencies]
openai = ["openai>=1.0.0"]  # Alternative embedding provider
```

## References

- [Voyage Code 3 Announcement](https://blog.voyageai.com/2024/12/04/voyage-code-3/) - 92% quality benchmarks
- [LanceDB docs](https://lancedb.github.io/lancedb/) - Hybrid search, Python API
- [Continue.dev + LanceDB](https://lancedb.com/blog/the-future-of-ai-native-development-is-local-inside-continues-lancedb-powered-evolution/) - Validates <10ms search latency
- [Code chunking research](https://supermemory.ai/blog/building-code-chunk-ast-aware-code-chunking/) - AST-aware chunking benefits
- [mgrep](https://github.com/mixedbread-ai/mgrep) - Competitive reference
- [Chonkie Documentation](https://docs.chonkie.ai) - AST-aware code chunking library
- [FastMCP SDK](https://github.com/modelcontextprotocol/python-sdk) - High-level MCP server API
- [DataStax 2025 Embedding Benchmark](https://dev.to/datastax/the-best-embedding-models-for-information-retrieval-in-2025-3dp5) - Voyage validation

## Alternative Embedding Providers

If Voyage Code 3 is unavailable or you prefer a single-vendor stack:

| Provider | Model | Quality | Cost | Context |
|----------|-------|---------|------|---------|
| **Voyage (default)** | voyage-code-3 | 92% | $0.18/1M | 32K |
| OpenAI (fallback) | text-embedding-3-large | ~85% | $0.13/1M | 8K |
| OpenAI (budget) | text-embedding-3-small | ~75% | $0.02/1M | 8K |

**Configuration:**
```yaml
# ~/.config/lgrep/config.yaml
embedding:
  provider: voyage  # or "openai"
  model: voyage-code-3  # or "text-embedding-3-large"
```

For OpenAI: `pip install lgrep[openai]` and set `OPENAI_API_KEY`.
