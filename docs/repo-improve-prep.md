# Research Pack: Repo Improvement Scan

Target: repo-wide
Mode: broad
Created: 2026-04-19
Updated: 2026-04-19

## Purpose & Scope

This pack covers repo-wide improvement opportunities for `lgrep` across current-state gaps, reference-architecture drift, and external landscape signals relevant to a dual-engine code-intelligence MCP server for OpenCode. It does not create ADV changes/tasks, does not modify spec state, and does not prescribe a single roadmap beyond evidence-backed next-step candidates.

## Current State

### Security

- **MEDIUM — Installer service template logs to `/tmp/lgrep.log`**
  - **Evidence:** `src/lgrep/install_opencode.py:79-97` sets `StandardOutput=append:/tmp/lgrep.log` and `StandardError=append:/tmp/lgrep.log`
  - **Impact:** Queries, errors, and operational details can land in a shared temp location instead of a user-scoped runtime/cache path.

### Reliability

- **HIGH — JSONC config path is detected but parsed as plain JSON**
  - **Evidence:** `src/lgrep/install_opencode.py:42-50` resolves `opencode.jsonc`; `src/lgrep/install_opencode.py:173-175` loads config with `json.loads(config_path.read_text())`
  - **Impact:** Valid OpenCode configs with comments/trailing commas can break `lgrep install-opencode` / `uninstall-opencode`.

- **LOW — Embed retry loops use blocking sleep**
  - **Evidence:** `src/lgrep/embeddings.py:143-150` and `src/lgrep/embeddings.py:190-198` call `time.sleep(delay)` inside retry paths
  - **Impact:** CLI calls and worker threads block during backoff; cancellation/responsiveness stays weaker than async-first retry handling.

### Testing

- **No significant gap observed in sampled areas**
  - **Evidence:** `tests/test_security.py` covers adversarial discovery cases; `tests/test_benchmark_latency.py` enforces latency budgets; `.github/workflows/ci.yml:10-40` runs Ruff + pytest on Python 3.11/3.12/3.13; targeted local verification passed (`53 passed`, `93 passed`, `ruff check src tests` clean on 2026-04-19).

### Observability

- **No major current-state gap, but logs-only posture**
  - **Evidence:** `src/lgrep/server.py:68-103` wraps tools with timed structured logging and timeout handling; `src/lgrep/server.py:1296-1309` configures JSON `structlog`
  - **Impact:** Good baseline diagnostics today; next step for production ops would be explicit metrics/health signals if HTTP deployment remains core.

### Developer Experience

- **MEDIUM — Referenced improve checklist is missing from repo**
  - **Evidence:** searched `docs/checklists/improve-checklist.md`; no matching file found in repo-wide glob; direct read failed at `/home/jrede/dev/oc-plugins/lgrep/docs/checklists/improve-checklist.md`
  - **Impact:** Improvement workflow is less reproducible because command contract references non-existent checklist.

- **MEDIUM — Transport guidance is internally split between stdio runtime default and HTTP-first install/docs path**
  - **Evidence:** `src/lgrep/server.py:1288` defaults server runtime to `stdio`; `README.md:191-203` recommends shared HTTP startup first; `README.md:337-349` says shared HTTP is intended deployment mode; `src/lgrep/install_opencode.py:7-15` states installer writes remote/HTTP config
  - **Impact:** Users get mixed signals on safest default vs recommended deployment path.

### Code Quality

- **MEDIUM — Duplicate semantic storage implementations create drift risk**
  - **Evidence:** repo contains both `src/lgrep/storage.py` and `src/lgrep/storage/_chunk_store.py`; `src/lgrep/storage/__init__.py:1-10` says package now re-exports `_chunk_store`; outlines show near-identical `ChunkStore`/`SearchResults` APIs in both files
  - **Impact:** Dead-or-stale duplication increases maintenance cost and future bug risk.

- **MEDIUM — `server.py` remains a large mixed-responsibility module**
  - **Evidence:** `src/lgrep/server.py` is 1324 lines and its outline contains 36 symbols spanning lifecycle, search orchestration, tool contracts, watcher control, and entrypoint logic
  - **Impact:** Harder review, higher regression risk, and slower architectural change.

## LBP / Reference Comparison

Reference source: Context7 `/modelcontextprotocol/python-sdk` README snippets for FastMCP transport, structured tool outputs, and lifespan management.

| Area | Current state | Verdict | Evidence | Reference | Minimum viable fix |
|---|---|---|---|---|---|
| Tool output contracts | Tools frequently return JSON strings via helpers | ANTI-PATTERN | `src/lgrep/server.py:107-112`, `src/lgrep/server.py:512-524` | FastMCP docs recommend returning structured dicts/Pydantic models directly | Replace stringified payloads with typed dict/Pydantic responses; centralize typed error schema |
| Lifespan/context management | Async lifespan + typed dataclass context | SOUND | `src/lgrep/server.py` outline: `LgrepContext`, `app_lifespan` | FastMCP lifespan example uses async context manager + typed app context | Keep current pattern |
| Input schema richness | Tool params use type hints + `pydantic.Field` descriptions | SOUND | `src/lgrep/server.py:542-549` and surrounding tool defs | FastMCP docs show typed tool schemas via type hints/Pydantic | Keep current pattern; extend to outputs |
| Transport posture | Runtime default is stdio, but docs/installer foreground HTTP shared mode | DRIFTED | `src/lgrep/server.py:1288`, `README.md:191-203`, `README.md:337-349`, `src/lgrep/install_opencode.py:7-15` | MCP examples show stdio default, streamable HTTP as explicit option/production config | Present stdio as safe default path; position shared HTTP as opt-in deployment mode with stronger auth/gateway guidance |

### Corrections

- **ANTI-PATTERN — JSON-string tool responses**
  - **What is wrong:** MCP tool handlers manually `json.dumps(...)` payloads and errors instead of returning structured objects.
  - **Where:** `src/lgrep/server.py:107-112`, `src/lgrep/server.py:512-524`, plus other tool handlers following same contract.
  - **What is correct:** FastMCP structured outputs via Pydantic models / typed dicts / typed primitives.
  - **Minimum fix:** Introduce response models for search results, status, and error payloads; migrate tool return signatures incrementally.

- **DRIFTED — transport recommendation hierarchy**
  - **What is wrong:** Product messaging pushes shared HTTP first even though runtime default and MCP examples treat stdio as default baseline.
  - **Where:** `README.md:191-203`, `README.md:337-349`, `src/lgrep/install_opencode.py:7-15`
  - **What is correct:** Show stdio as default low-friction path; document streamable HTTP as opt-in for warm shared-server deployments with explicit security controls.
  - **Minimum fix:** Reframe README/install text, add “quick start: stdio” and “scale-up: shared HTTP” sections.

### Greenfield Notes

- Split `server.py` into lifecycle/state, semantic tools, symbol tools, and transport/bootstrap modules.
- Make MCP responses typed-first from day one instead of JSON-string-first.
- Treat shared HTTP as deployment tier with gateway/auth/health guidance, not default onboarding path.
- Remove legacy `src/lgrep/storage.py` once package-based storage API is confirmed stable.

## Competitors & Alternatives

| Competitor / alternative | What it does differently | Source URL | Relevance to this repo |
|---|---|---|---|
| mgrep | Pure semantic search for agents with simpler “semantic grep” positioning | https://www.mgrep.dev/ | Closest semantic-search competitor; validates demand, but also pressures `lgrep` to keep its semantic UX sharper while preserving symbol/outline advantage |
| Augment Context Engine | Broader coding-agent context platform, not only local retrieval | https://www.augmentcode.com/blog/context-engine-mcp-now-live | Strong alternative for teams wanting full context platform rather than focused MCP retrieval tool; pushes `lgrep` to clarify local-first niche |
| Code Pathfinder MCP | Privacy-first local MCP code intelligence with call graphs, dependency tracing, and AST-heavy analysis | https://codepathfinder.dev/mcp | Highlights opportunity for deeper structural analysis beyond symbol lookup, especially for Python-heavy repos |

## Emerging Patterns

- **Local-first / privacy-first code intelligence**
  - **Maturity signal:** recurring 2026 discussion around local-first AI tooling and privacy-first MCP servers
  - **Source URL:** https://codepathfinder.dev/mcp
  - **Why noteworthy:** reinforces `lgrep`’s local-storage and no-API-key symbol-engine story as differentiator, not side detail

- **MCP deployment gateways, permissioning, and security hardening**
  - **Maturity signal:** dedicated 2026 platform/gateway comparisons and permissioning guidance indicate operational layer is becoming product surface area
  - **Source URL:** https://www.prefect.io/resources/best-mcp-deployment-platforms-enterprise-2026
  - **Why noteworthy:** if shared HTTP remains central, `lgrep` will need stronger deployment/auth story than “bind localhost and add proxy if needed”

## Applicability to This Repo

- **Applies strongly:** deeper structural/code-graph analysis inspired by Code Pathfinder would complement existing symbol tools (`src/lgrep/tools/*`, `src/lgrep/parser/*`).
- **Applies strongly:** stronger local-first/privacy positioning should be surfaced earlier in README (`README.md:40-85`, `README.md:87-123`) because market signal supports it.
- **Applies selectively:** gateway/auth/deployment guidance matters only if HTTP remains first-class (`README.md:191-203`, `README.md:337-349`, `src/lgrep/install_opencode.py:79-97`).
- **Less applicable:** full-platform competitive moves from Augment are broader than `lgrep`’s focused tool scope; repo should avoid bloating into a general coding-agent platform.

## Open Questions for Research

- Should `lgrep` keep shared HTTP as primary recommended deployment, or demote it behind stdio + explicit scale-up guidance?
- Is typed MCP output migration worth a compatibility layer, or should it ship as a clean breaking change in next minor/major release?
- Can `src/lgrep/storage.py` be deleted immediately, or are there downstream consumers relying on file-level imports or packaging side effects?
- Should installer support JSONC properly via parser dependency, or avoid `.jsonc` mutation entirely and emit manual instructions instead?
- Is deeper code-graph/dataflow analysis within scope, or should `lgrep` stay retrieval-focused and integrate with a complementary MCP server instead?

## Sources

- /modelcontextprotocol/python-sdk
- https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md
- https://www.mgrep.dev/
- https://www.augmentcode.com/blog/context-engine-mcp-now-live
- https://codepathfinder.dev/mcp
- https://www.prefect.io/resources/best-mcp-deployment-platforms-enterprise-2026
- https://medium.com/@lssmj2014/github-trending-january-16-2026-superpowers-phenomenon-local-first-ai-4dc2b02e173a
