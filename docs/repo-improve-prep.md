# Research Pack: Repo Improvement Scan

Target: repo-wide  
Mode: broad  
Created: 2026-04-19  
Updated: 2026-05-27

## Purpose & Scope

This pack covers repo-wide improvement opportunities for `lgrep`, a Python 3.11+ MCP server combining semantic code search (Voyage Code 3 + LanceDB) and symbol intelligence (tree-sitter indexes) for OpenCode/agent workflows.

Scope refreshed 2026-05-27:

- current-state scan across Security, Reliability, Testing, Observability, Developer Experience, and Code Quality
- LBP/reference comparison for FastMCP/MCP server lifecycle, streamable HTTP deployment, and Python async/thread behavior
- external landscape for MCP code-intelligence/search tools in 2026
- operator ticket evidence for lgrep 3.1.0 shared-subprocess CPU/thread runaway under Vision MCP

Non-scope:

- no ADV changes, tasks, gates, agenda items, or spec edits
- no implementation changes outside this research pack
- no destructive cache cleanup or daemon restart

Context snapshot:

- `adv_project_context`: no `project.md` present
- active ADV changes: none
- pending agenda items: none
- specs: `lgrepSemanticCacheLifecycle` v1.0.0, `lgrepToolSelectionOptimization` v1.2.1
- source roots/manifests: `src/lgrep/`, `tests/`, `pyproject.toml`, `.github/workflows/ci.yml`

## Current State

### Security

- **LOW — systemd instructions encourage inline API key in service unit**
  - **Evidence:** `src/lgrep/install_opencode.py:80-94` renders `Environment=VOYAGE_API_KEY={api_key_placeholder}` directly into the user service template; `src/lgrep/install_opencode.py:115-137` prints that template/manual command for copy-paste.
  - **Impact:** Local user service files are not public, but inline secrets are harder to rotate and easier to expose in support bundles than an `EnvironmentFile=` with permissions guidance.
  - **Recommendation:** Emit an env-file based systemd pattern (`EnvironmentFile=%h/.config/lgrep/env`) with `chmod 600` instructions; keep inline env only as quick manual example.
  - **Follow-up:** `/adv-task` or `/adv-proposal Harden service secret handling`

- **POSITIVE — destructive MCP prune is transport-safe**
  - **Evidence:** `src/lgrep/server/tools_maintenance.py:31-55` treats unknown/non-stdio transports as non-local; `src/lgrep/server/tools_maintenance.py:58-102` coerces `dry_run=True` for shared transports.
  - **Impact:** Shared HTTP deployments cannot delete cache directories through MCP accidentally.

### Reliability

- **CRITICAL — shared daemon has no structural active-job accounting/cancellation for expensive threaded work**
  - **Evidence:** operator ticket (2026-05-27) reports lgrep 3.1.0 in Vision shared subprocess mode sustaining ~318-325% CPU for >1h with 140 threads and global `lgrep_status_semantic(path="")` timing out after 8s. Local code routes expensive work through `asyncio.to_thread` / default executors without job registry or cancellation tokens: `src/lgrep/server/tools_semantic.py:125-147`, `src/lgrep/server/tools_semantic.py:287-290`, `src/lgrep/server/lifecycle.py:376-381`, `src/lgrep/watcher.py:97-115`.
  - **Impact:** When MCP clients disconnect/reconnect, coroutine cancellation/timeouts do not necessarily stop already-running synchronous indexing/search/storage operations, so shared-daemon CPU and threads can remain hot after callers vanish.
  - **Recommendation:** Introduce per-project job registry + cancellation-aware indexing/search boundaries; bound executor workers; expose active job diagnostics; cancel/mark abandoned jobs on session/tool timeout where safe.
  - **Follow-up:** `/adv-proposal Fix lgrep daemon runaway`

- **HIGH — global semantic status fans out over every loaded project and can block until timeout**
  - **Evidence:** `src/lgrep/server/tools_semantic.py:377-384` calls `_get_project_stats` for every `app_ctx.projects` entry via `asyncio.gather`; `_get_project_stats` opens/counts LanceDB state via `asyncio.to_thread` in `src/lgrep/server/lifecycle.py:306-333`. Operator ticket reports per-project status works but global status times out with many stale repo/worktree/temp keys loaded.
  - **Impact:** A diagnostic/status call can become as expensive as scanning all loaded cache state, fail exactly when operators need it, and add more thread work to an already overloaded daemon.
  - **Recommendation:** Make global status bounded and cheap by default: return in-memory counters, project count, stale/error summary, and top-N/details pagination; put deep per-project disk stats behind an explicit `deep=true` or per-path call.
  - **Follow-up:** `/adv-proposal Bound global status cost`

- **HIGH — startup auto-warm defaults to discovering cached projects unless disabled**
  - **Evidence:** `src/lgrep/server/lifecycle.py:482-527` auto-discovers disk caches via `discover_cached_projects(max_results=MAX_PROJECTS)` when `LGREP_WARM_PATHS` is unset and `LGREP_AUTO_WARM_DISK` defaults to true. README later recommends `LGREP_AUTO_WARM_DISK=false` for Vision/OpenCode tuning at `README.md:432-452`.
  - **Impact:** Shared servers can load stale temp/worktree projects on startup by default, inflating in-memory project count and global status cost.
  - **Recommendation:** For shared HTTP/Vision mode, default to explicit warm paths or make disk auto-warm opt-in; at minimum warn when auto-discovered paths include deleted/tmp/worktree-looking entries.
  - **Follow-up:** `/adv-proposal Make shared warm explicit`

- **MEDIUM — worktree dedup intentionally leaves stale deleted-file chunks**
  - **Evidence:** `src/lgrep/indexing.py:80-95` skips stale-file deletion when `LGREP_WORKTREE_DEDUP` is enabled; README documents the tradeoff at `README.md:504-507`.
  - **Impact:** Search can surface extra stale results in shared-cache mode. This is documented and safer than corrupting other worktrees, but needs freshness/status visibility so users can distinguish benign stale extras from daemon pathology.
  - **Recommendation:** Add per-result/cache freshness markers or a `shared_cache_stale_files_estimate` diagnostic rather than silently relying on docs.
  - **Follow-up:** `/adv-task` or `/adv-audit`

### Testing

- **HIGH — no regression coverage for disconnected MCP clients leaving in-flight threaded jobs alive**
  - **Evidence:** tests contain startup-sweep cancellation coverage (`tests/test_worktree_cache.py:369-404`) and timeout coverage for slow async query embedding (`tests/test_server.py:1245-1279`), but text search found no test for global status timeout/cancellation or client disconnect against `asyncio.to_thread` work (`lgrep_search_text` over tests for `global status`, `cancel`, `to_thread`).
  - **Impact:** The reported daemon runaway can recur without failing CI.
  - **Recommendation:** Add deterministic tests for: tool timeout does not leave job registered/running; global status caps work; disconnect/reap cancels or detaches abandoned jobs; executor worker count is bounded.
  - **Follow-up:** `/adv-proposal Fix lgrep daemon runaway`

- **POSITIVE — CI spans supported Python versions and lint/format**
  - **Evidence:** `.github/workflows/ci.yml:36-59` runs pytest on Python 3.11/3.12/3.13; `.github/workflows/ci.yml:13-35` runs Ruff check and format check.
  - **Impact:** Baseline compatibility and style regressions are covered.

### Observability

- **HIGH — daemon lacks operator-facing active job / thread / PID diagnostics**
  - **Evidence:** response contracts include status fields for `files`, `chunks`, `watching`, `project`, `disk_cache`, and `error` only (`src/lgrep/server/responses.py:117-132`). The ticket explicitly requests active indexing jobs, active semantic searches, repo consuming CPU, task age, cancellation state, PID, and worker/task stats.
  - **Impact:** Operators can see “global status timed out” but cannot identify which project/job is burning CPU or whether abandoned work remains after disconnect.
  - **Recommendation:** Add `lgrep_diagnostics` or extend status with lightweight runtime counters: pid, uptime, executor worker count, active jobs by type/project/age, queued jobs, loaded aliases, last timeout/error.
  - **Follow-up:** `/adv-proposal Add lgrep daemon diagnostics`

- **MEDIUM — timeout error message over-attributes to re-indexing/Voyage**
  - **Evidence:** `src/lgrep/server/__init__.py:38-51` and `src/lgrep/server/responses.py:354-365` both emit “The project may need re-indexing or the Voyage API may be slow” for any timed-out tool.
  - **Impact:** A global-status or storage/thread-pool starvation timeout gets misleading remediation; operators may re-index and worsen load.
  - **Recommendation:** Include tool-specific timeout causes/remediation and log/return a timeout classification.
  - **Follow-up:** `/adv-task`

### Developer Experience

- **MEDIUM — no repo project context for agents**
  - **Evidence:** `adv_project_context` returned “No project context file found at project.md”.
  - **Impact:** ADV/OpenCode agents must infer architecture from README/source each run, increasing drift and duplicated analysis.
  - **Recommendation:** Add `project.md` with stack, runtime modes, test commands, Vision/OpenCode deployment assumptions, and current daemon incident notes.
  - **Follow-up:** `/adv-task`

- **MEDIUM — shared HTTP is still positioned as intended OpenCode deployment while current incident shows local shared-daemon risk**
  - **Evidence:** README says shared HTTP is intended for OpenCode at `README.md:247-260` and `README.md:400-414`, while Vision tuning later says disable broad auto-warm and lower timeouts at `README.md:432-457`; operator ticket reports shared Vision subprocess sustained 300%+ CPU with reconnect churn.
  - **Impact:** Users may choose the risky deployment tier before they understand required tuning/diagnostics.
  - **Recommendation:** Reframe docs: stdio/local default, shared HTTP/Vision as scale-up with explicit resource limits, health checks, and cleanup playbook.
  - **Follow-up:** `/adv-proposal Harden shared deployment docs`

### Code Quality

- **HIGH — `CHANGELOG.md` contains unresolved merge conflict markers**
  - **Evidence:** `CHANGELOG.md:1-10` contains `<<<<<<< HEAD`, `=======`; `lgrep_search_text(query="<<<<<<<")` found two markers at `CHANGELOG.md:1` and `CHANGELOG.md:6`.
  - **Impact:** Release documentation is visibly corrupted and CI does not catch conflict markers in markdown.
  - **Recommendation:** Resolve changelog conflict and add a repo-wide conflict-marker guard to CI/pre-commit or tests.
  - **Follow-up:** `/adv-task`

- **MEDIUM — duplicate timeout decorator definitions invite drift**
  - **Evidence:** `src/lgrep/server/__init__.py:22-57` defines `TOOL_TIMEOUT_S` and `time_tool`; `src/lgrep/server/responses.py:34-39` and `src/lgrep/server/responses.py:334-371` define a second `TOOL_TIMEOUT_S` and `time_tool` with similar behavior. Semantic tools import `time_tool` from `lgrep.server` at `src/lgrep/server/tools_semantic.py:14`, not from `responses.py`.
  - **Impact:** Timeout behavior and tests can patch one location while another copy diverges; response-contract locality is weaker than intended.
  - **Recommendation:** Keep one canonical timeout decorator/constant and re-export it; delete the duplicate or make one a direct alias.
  - **Follow-up:** `/adv-task`

## LBP / Reference Comparison

Reference sources:

- Context7 `/modelcontextprotocol/python-sdk` README snippets for FastMCP lifespan, Streamable HTTP, and structured output
- Python docs: `asyncio` coroutines/tasks, `asyncio.to_thread`, cancellation/introspection, and `concurrent.futures.ThreadPoolExecutor`
- FastMCP HTTP deployment docs from `https://gofastmcp.com/deployment/http`

| Area | Current state | Reference | Classification | Minimum viable fix | Greenfield note |
|---|---|---|---|---|---|
| FastMCP lifespan | `app_lifespan` initializes context, warms projects, schedules sweep, cancels sweep on shutdown (`src/lgrep/server/lifecycle.py:157-168`) | MCP Python SDK shows typed app context via async context manager and cleanup in `finally` | SOUND | Keep pattern; extend cleanup to active job registry/executors | Design all long-lived resources under one context-managed runtime supervisor |
| Structured MCP output | TypedDict response contracts in `src/lgrep/server/responses.py:1-17`; README documents dict response at `README.md:324-353` | MCP Python SDK structured-output examples return dicts validated/serialized by server | SOUND | Keep TypedDict convention; consider runtime validation for critical response contracts | Generate schemas from Pydantic models where runtime validation matters |
| Streamable HTTP deployment | README recommends shared HTTP for multi-session and warns no auth/default localhost (`README.md:247-260`, `README.md:400-414`) | FastMCP HTTP docs emphasize ASGI deployment control, stateless HTTP for scaling, reverse proxy/auth/CORS/TLS controls | DRIFTED | Add “shared daemon hardening” checklist: explicit warm paths, disabled auto-warm, timeout below proxy, auth/proxy guidance, CPU/thread watchdog | Treat shared server as production service with health/metrics/jobs endpoints from day one |
| Threaded sync work from async tools | Many handlers use `asyncio.to_thread` / executor calls for DB/index/search (`src/lgrep/server/tools_semantic.py:125-147`, `src/lgrep/server/lifecycle.py:306-333`, `src/lgrep/watcher.py:97-115`) | Python docs: `to_thread` is for running blocking functions without blocking event loop; cancellation of awaiting coroutine does not guarantee synchronous worker code is stopped; ThreadPoolExecutor needs bounded lifecycle and shutdown | ANTI-PATTERN | Replace ad-hoc default-executor usage for expensive jobs with named bounded executor + cooperative cancellation/job state | Use an explicit worker supervisor/queue per project with cancellation tokens and backpressure |
| Global status behavior | All-project status gathers deep stats for every loaded project (`src/lgrep/server/tools_semantic.py:377-384`) | Operational LBP for health endpoints: cheap, bounded, non-invasive; deep checks explicit | ANTI-PATTERN | Make `status_semantic(path="")` O(loaded projects) over in-memory metadata only; paginate/deep-check separately | Split `/status`, `/diagnostics`, and `/inspect-project` from first release |
| Cache pruning / stale alias cleanup | `prune_orphans` and `gc_worktree_meta` exist with guards (`src/lgrep/tools/prune_orphans.py:197-281`, `src/lgrep/tools/prune_orphans.py:387-500`) | Spec `lgrepSemanticCacheLifecycle` requires stable orphan reasons, dry-run default, guards, transport safety | SOUND with ops gap | Add scheduled/diagnostic surfacing for stale aliases and active skip reasons | Include cache health summary in daemon status/diagnostics |

### Corrections

- **ANTI-PATTERN — unbounded/uncancellable expensive thread work in shared daemon**
  - **Wrong path:** `src/lgrep/server/tools_semantic.py`, `src/lgrep/server/lifecycle.py`, `src/lgrep/watcher.py`
  - **Correct pattern:** bounded executor or worker queue; per-job metadata; cooperative cancellation; explicit shutdown; status/diagnostics separate from work submission.
  - **Minimum fix:** introduce `RuntimeJobRegistry` and `BoundedExecutor` in server lifecycle; wrap index/search/status jobs; expose read-only diagnostics; ensure timeout cleanup removes/marks abandoned jobs.

- **ANTI-PATTERN — global status performs deep per-project disk work**
  - **Wrong path:** `src/lgrep/server/tools_semantic.py:377-384`
  - **Correct pattern:** health/status endpoints are cheap and bounded; expensive checks are opt-in and scoped.
  - **Minimum fix:** default global status returns loaded project keys + cached counters + warning when deep data omitted; add `path` or explicit deep status for detailed LanceDB counts.

- **DRIFTED — shared HTTP docs understate daemon-hardening requirements**
  - **Wrong path:** `README.md:247-260`, `README.md:400-414`, balanced partly by `README.md:432-457`
  - **Correct pattern:** shared daemon treated as ops surface with bounded resources, diagnostics, health checks, and security/tuning defaults.
  - **Minimum fix:** promote Vision/OpenCode tuning earlier and add incident troubleshooting checklist.

## Competitors & Alternatives

| Name | Summary | Difference | Maturity / signal | Source | Relevance |
|---|---|---|---|---|---|
| Sverklo | Hybrid local MCP code-intelligence server with BM25 + embeddings + PageRank/RRF and benchmark-oriented comparison. | Adds graph/ranking/memory dimensions beyond lgrep's semantic + symbol split. | 2026 comparison positions hybrid retrieval as category direction. | https://sverklo.com/blog/practical-guide-mcp-code-intelligence/ | Medium — validates hybrid direction; useful benchmark/positioning pressure. |
| jCodeMunch MCP | Symbol-first MCP server focused on precise retrieval, call graphs, blast radius, dead code, tests, and hotspots. | Deeper structural/code-graph diagnostics than lgrep's current symbol outline/get-symbol tools. | Public comparison pages and GitHub activity; cited as strong definition lookup alternative. | https://github.com/jgravelle/jcodemunch-mcp | High — suggests next durable differentiator is diagnostics/graph, not only search. |
| Nexus-MCP / local-first hybrid code-intelligence tools | Local-first server combining vector, BM25, graph, semantic memory, and low RAM target. | Zero cloud dependency and bounded resource profile are central product claims. | 2026 GitHub repo positions <350MB RAM and no API keys as core. | https://github.com/jaggernaut007/nexus-mcp | High — directly relevant to current shared-daemon CPU/resource incident. |

## Emerging Patterns

- **Hybrid retrieval + graph + ranking as default code-intelligence stack**
  - **Source:** https://chatforest.com/reviews/code-intelligence-codebase-graph-mcp-servers/
  - **Summary:** 2026 MCP code-intelligence reviews describe convergence on BM25 + vector + graph/PageRank-style retrieval.
  - **Applicability:** Medium. lgrep already has vector + keyword hybrid and symbols; graph/risk diagnostics could be added selectively after daemon stability.

- **Local-first, bounded-resource MCP daemons**
  - **Source:** https://github.com/jaggernaut007/nexus-mcp and https://github.com/jeremymefford/agent-context-mcp
  - **Summary:** Newer tools foreground local shared endpoints, no/low cloud dependency, bounded warm caches, and explicit resource budgets.
  - **Applicability:** High. lgrep's current differentiation depends on being safe as always-on local infrastructure.

## Applicability to This Repo

Applies now:

- Fix daemon runaway first: it is both current operator pain and architectural foundation for shared HTTP/Vision positioning.
- Make global status cheap and add diagnostics before deeper product features; current observability cannot isolate CPU-consuming projects/jobs.
- Resolve `CHANGELOG.md` conflict markers immediately; it is low-risk and high-signal release hygiene.
- Add conflict-marker CI guard and daemon cancellation/status regression tests so the same issue does not return silently.

Applies selectively:

- Graph/call-impact features from competitors are promising, but should wait until runtime resource boundaries are structural.
- Local embeddings/no-cloud mode would strengthen privacy/resource positioning, but is a larger product strategy decision because lgrep currently optimizes around Voyage Code 3 retrieval quality.

Does not apply now:

- Enterprise/full-platform context engines are broader than lgrep's focused retrieval infrastructure mission.
- Public internet production deployment patterns are less urgent than localhost/Vision shared-subprocess hardening for this user base.

## Open Questions for Research

- Should shared HTTP/Vision mode get different defaults (`LGREP_AUTO_WARM_DISK=false`, bounded executor size, explicit warm paths) from stdio mode?
- What is the safest cancellation model for LanceDB/Voyage/indexing work that already started in a thread?
- Should `status_semantic(path="")` preserve exact current response shape and add summary fields, or should a new `diagnostics` tool carry bounded status?
- What thread/CPU thresholds should trigger watchdog warnings on typical WSL/Linux OpenCode hosts?
- Should lgrep add local embedding backend support, or stay Voyage-first and compete on retrieval quality plus local cache/symbols?

## Sources

- User-provided operator ticket in this ADV Improve run (2026-05-27): lgrep 3.1.0, Vision shared subprocess, PID 36091, 300%+ CPU, 140 threads, global status timeout
- `/modelcontextprotocol/python-sdk` via Context7 — FastMCP lifespan, Streamable HTTP, structured output examples
- https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md
- https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread
- https://docs.python.org/3/library/asyncio-task.html#task-cancellation
- https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor
- https://gofastmcp.com/deployment/http
- https://sverklo.com/blog/practical-guide-mcp-code-intelligence/
- https://github.com/jgravelle/jcodemunch-mcp
- https://j.gravelle.us/jCodeMunch/versus.php
- https://chatforest.com/reviews/code-intelligence-codebase-graph-mcp-servers/
- https://github.com/jaggernaut007/nexus-mcp
- https://github.com/jeremymefford/agent-context-mcp
- Local evidence: `pyproject.toml`, `README.md`, `CHANGELOG.md`, `src/lgrep/server/*`, `src/lgrep/tools/prune_orphans.py`, `src/lgrep/watcher.py`, `src/lgrep/indexing.py`, `tests/*`, `.github/workflows/ci.yml`
