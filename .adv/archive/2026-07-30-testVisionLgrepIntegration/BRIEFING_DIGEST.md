# Archive Briefing Digest

**Change ID:** testVisionLgrepIntegration
**Title:** Test Vision lgrep integration
**Status:** archived
**Generated:** 2026-07-30T05:35:25.462Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY

## Archive Digest

**Status:** archived

| Gate | Status |
| --- | --- |
| proposal | done |
| discovery | done |
| design | done |
| planning | done |
| execution | done |
| acceptance | done |
| release | pending |

## Epic Context

No Epic membership

## Durable Facts

Showing 36 of 36 durable facts.

- **[report_follow_up]** follow_ups: GAP: add test_search_references_invalid_kind_error (symmetric with invalid_usage_filter; kind validation exists at src/lgrep/tools/search_references.py:67-71 but is untested).
- **[report_follow_up]** follow_ups: GAP: add test_search_references_timeout_keeps_schema_shape (time_tool timeout branch coded at src/lgrep/server/__init__.py:58-68 but untested; only cancellation is tested).
- **[report_follow_up]** follow_ups: GAP: add corrupt-index behavior test - write invalid JSON to index_<hash>.json, assert load returns None and search_references surfaces the generic 'Repository not indexed' error (no distinct corrupt signal; confirm this collapse is intended).
- **[report_follow_up]** follow_ups: LATENT: MCP success-path _meta.duration_ms is hardcoded 0.0 (src/lgrep/server/tools_symbols.py:566) while real timing is logged via structlog - consider injecting measured duration or document the envelope contract.
- **[report_follow_up]** follow_ups: LATENT: symbol index uses normalize_repo_key=resolved path (src/lgrep/storage/index_store.py:51-59) and does NOT dedup across git worktrees, unlike the semantic cache (LGREP_WORKTREE_DEDUP canonical_repo_key). Add a behavioral note/test and decide if parity is wanted for multi-worktree agent setups.
- **[report_follow_up]** follow_ups: AMBIGUITY: production_first and include_tests produce identical ordering (src/lgrep/tools/search_references.py:119-136) - confirm whether the naming distinction is intentional or include_tests should behave differently.
- **[report_follow_up]** follow_ups: GAP: no version-drift handling - CodeIndex.version is parsed (_version_tuple) but never gated; an incompatible future version loads silently. Decide if a version check belongs in load or search_references.
- **[report_follow_up]** follow_ups: GAP: add a diagnostics-integration test asserting a search_references job appears in lgrep_diagnostics recent_jobs (kind/caller/project/status) and feeds timeout_abandonment_summary.
- **[research_citation]** sources: search_references tool source: Tool impl: _USAGE_FILTERS={production_first,include_tests,tests_only}, _OCCURRENCE_KINDS={call,attribute,import,reference}, MAX_REFERENCE_RESULTS=100, limit default 20 (min coerced 1, capped at 100), _DISCLAIMER text. Error paths: empty query, invalid usage_filter, invalid kind, index None (unindexed), not index.occurrences (stale). production_first and include_tests share IDENTICAL sort; only tests_only filters the set. (src/lgrep/tools/search_references.py)
- **[research_citation]** sources: MCP handler registration: search_references @mcp.tool with ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False) (destructiveHint unset=False). ctx!=None -> app_ctx.runtime.run_blocking(kind=caller='search_references', project=resolved_path, fn=_search_references, query, ORIGINAL path, limit=, usage_filter=, kind=); ctx=None -> asyncio.to_thread. No cancel_event passed. On error -> error_response(result['error']). _meta hardcoded {'duration_ms':0.0,'tool':'search_references'} on success (rich direct _meta discarded). (src/lgrep/server/tools_symbols.py:480-567)
- **[research_citation]** sources: time_tool decorator (timeout/cancel): TOOL_TIMEOUT_S=env LGREP_TOOL_TIMEOUT_S default 45 (Vision tuning sets 8). Wraps async tools in asyncio.wait_for. TimeoutError: search_references has special schema-stable branch (coded lines 58-68, NOT tested). CancelledError: search_references schema-stable branch returns error='Operation was cancelled.' (TESTED). _meta={duration_ms,tool} at MCP layer. (src/lgrep/server/__init__.py:23,26-93)
- **[research_citation]** sources.omitted: 12 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: The search_references tool is a bounded, read-only, in-memory candidate-occurrence lookup over a JSON symbol index. The managed-MCP (Vision) integration path is the ctx!=None branch routing through RuntimeSupervisor.run_blocking; it is real, live (tools.lgrep.search_references callable now), and already lightly tested. The validation matrix maps cleanly onto 13 dimensions with concrete measurable signals. Key behavioral findings the matrix must encode: (1) production_first and include_tests are observationally identical (same sort, both retain all groups) - only tests_only filters; (2) direct-tool _meta ({timing_ms,tokens_saved,...}) differs from MCP-layer _meta ({duration_ms,tool}) and the MCP success-path duration_ms is hardcoded 0.0; (3) normalize_repo_key uses resolved absolute path, NOT the semantic worktree-dedup canonical key - each worktree needs its own symbol index (divergence from semantic cache); (4) corrupt JSON index collapses into the generic unindexed error; (5) no version-gate despite a version field; (6) two timeout layers (Vision proxy ~8s + lgrep TOOL_TIMEOUT_S, default 45s/8s under Vision) and search_references has NO cancel_event threading (abandoned jobs run to completion then mark FINISHED_AFTER_ABANDON). No blockers to building the matrix; gaps are test-coverage improvements, not correctness blockers for the integration itself.
- **[report_follow_up]** follow_ups: Docs drift: instructions/lgrep-tools.md:71 and skills/lgrep/SKILL.md correctly reference Vision, but ADV change multiProjectSupportConcurrentI/change.json:161 incorrectly recorded 'lgrep is not in the Vision MCP server registry' (a failed vision_search). Live servers.yaml contradicts that -- consider correcting the stale ADV note.
- **[report_follow_up]** follow_ups: Add a one-line comment in install_opencode.py noting the Vision (:6278) vs standalone (:6285) port split so operators avoid running the repo installer on a Vision-managed host.
- **[report_follow_up]** follow_ups: Consider a tiny deploy helper (uv reinstall + vision_restart lgrep + health check) to encode the binary-upgrade path and prevent the stale-binary-on-reload footgun.
- **[report_follow_up]** follow_ups: Verify whether a no-op servers.yaml touch + `vision daemon reload` reliably restarts lgrep; if not, document `vision_restart lgrep` as the only binary-cutover mechanism.
- **[research_citation]** sources: uv-receipt.toml (installed tool record): requirements=[{name=lgrep, directory=/home/jon/dev/lgrep}]; entrypoint install-path=/home/jon/.local/bin/lgrep. Proves uv-tool install from local source dir. (file:///home/jon/.local/share/uv/tools/lgrep/uv-receipt.toml)
- **[research_citation]** sources: ~/.local/bin/lgrep console-script wrapper: shebang #!/home/jon/.local/share/uv/tools/lgrep/bin/python; imports lgrep.cli:main. Standard uv-tool-generated launcher. (file:///home/jon/.local/bin/lgrep)
- **[research_citation]** sources: tool venv site-packages (editable check): Contains real lgrep/ + lgrep-3.2.0.dist-info/ (COPIED wheel). NO __editable__.*.pth. Installed version 3.2.0. Not editable -> source edits need reinstall. (file:///home/jon/.local/share/uv/tools/lgrep/lib/python3.13/site-packages/)
- **[research_citation]** sources.omitted: 12 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: DEPLOY PATH (source -> binary): lgrep installed via `uv tool` from LOCAL git source dir /home/jon/dev/lgrep. uv-receipt.toml: {name=lgrep, directory=/home/jon/dev/lgrep}; launcher /home/jon/.local/bin/lgrep is uv-generated console script (shebang into ~/.local/share/uv/tools/lgrep/bin/python). Install is a COPIED wheel (site-packages has real lgrep/ + lgrep-3.2.0.dist-info/, NO __editable__ .pth), version 3.2.0 -> source edits do NOT propagate until reinstall. Safe redeploy after code change: `uv tool upgrade lgrep --reinstall` (or `uv tool install --force --from /home/jon/dev/lgrep lgrep`) then restart the Vision subprocess. VISION LIFECYCLE: lgrep IS Vision-managed. servers.yaml registers lgrep port 6278, command=/home/jon/.local/bin/lgrep (stdio), autostart, session_timeout 30m, max_sessions 20, env VOYAGE_API_KEY/LGREP_WORKTREE_DEDUP=1/LGREP_WARM_PATHS(6 repos)/LGREP_AUTO_WARM_DISK=false/LGREP_TOOL_TIMEOUT_S=30/LGREP_WORKER_MAX_THREADS=2. Vision proxies OpenCode (mcp.lgrep -> http://localhost:6278/mcp) to a per-session lgrep stdio subprocess. NO lgrep.service systemd unit exists; Vision's supervisor owns the lgrep process. Vision itself = systemd user unit vision.service (vision daemon start; SIGHUP=reload). RELOAD/RESTART: (1) config edit -> `vision config validate` -> `vision daemon reload` (or systemctl --user reload vision); Reload() restarts only servers whose servers.yaml diffed, with rollback-on-start-failure. (2) wedged/stale subprocess -> `vision_restart lgrep` MCP tool (POST /servers/lgrep/restart) or admin stop/start. CRITICAL: a binary-only change (uv reinstall) yields NO servers.yaml diff, so `vision daemon reload` does NOT restart running lgrep -> stale binary serves until explicit `vision_restart lgrep` OR 30m idle reap respawns. HEALTH: `vision daemon status`; `vision config show`; admin HTTP GET :6275/health, /v1/servers, /v1/servers/lgrep; lgrep-internal `lgrep_diagnostics` (worker_max_threads/active_jobs/recent_jobs/timeout_abandonment_summary) and `lgrep_status_semantic(path='')`; `journalctl --user -u vision`. ROLLBACK: uv keeps no version history; medium is git: `cd /home/jon/dev/lgrep && git checkout <prev-tag> && uv tool upgrade lgrep --reinstall && vision_restart lgrep`. CONFIG OWNERSHIP: servers.yaml (Vision) owns port/command/env/tuning/session policy; opencode.jsonc mcp.lgrep owns only client URL (:6278)+enabled; repo instructions/skills deployed to ~/.config/opencode/{instructions,skills} by `lgrep install-opencode` (NOT the path used on this host).
- **[report_follow_up]** follow_ups: Precision AC2: fixture must hold >=100 occurrences of the queried symbol (or request limit>>100 and assert truncation to MAX_REFERENCE_RESULTS=100) so the 100-cap is observable.
- **[report_follow_up]** follow_ups: Precision AC3: search_references is a fast pure-Python index read and won't time out naturally; state induction (client abort/transport disconnect for cancellation) and explicitly invoke AC3 'documented transport-level equivalent' for timeout.
- **[report_follow_up]** follow_ups: Precision AC4: config sets LGREP_WORKER_MAX_THREADS=2; assert worker count <=2 and >=3 of 5 concurrent queued/abandoned against lgrep_diagnostics.worker_max_threads + active_jobs/recent_jobs, not only completion.
- **[report_follow_up]** follow_ups: Lifecycle: add main-freshness check (oc-fresh status / git fetch) to design step 2 before uv-tool reinstall, per deploy-from-trunk policy.
- **[report_follow_up]** follow_ups: Boundary: clarify setup-phase (construct stale-occurrence index) is permitted and separate from verification-phase read-only MCP calls (C3); note temp-fixture index leaves a benign stale symbol-store entry.
- **[report_follow_up]** follow_ups: Highest uncertainty for AC5: could not run git from this read-only context; confirm a 3.1.x tag/commit is actually installable for rollback before relying on it.
- **[research_citation]** sources: search_references source (main): MAX_REFERENCE_RESULTS=100 (L23); _DISCLAIMER (L25-27); usage_filter in {production_first,include_tests,tests_only} (L21); kind in {call,attribute,import,reference} (L22); empty/invalid/unindexed/stale error paths (L57-92). Confirms AC2 surface + most AC3 error paths on main. (src/lgrep/tools/search_references.py)
- **[research_citation]** sources: Live Vision lgrep config: LGREP_TOOL_TIMEOUT_S=30 (L57); LGREP_WORKER_MAX_THREADS=2 (L59); session_timeout=30m; max_sessions=20. Deployed tool timeout is 30s, NOT 8s. (/home/jon/.config/vision/servers.yaml (lgrep, L49-62))
- **[research_citation]** sources: CHANGELOG -- search_references Unreleased; 8s historical: lgrep_search_references under '## Unreleased' (L5); pyproject=3.2.0; v3.1.8 (L23), v3.1.3 (L33). v3.1.3 entry references historical '8s tool timeout' (L37) -- origin of the 8s figure, predating current 30s config. (/home/jon/dev/lgrep/CHANGELOG.md L1-5, L33-37)
- **[research_citation]** sources.omitted: 5 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: STRUCTURALLY SOUND, EXECUTABLE, WITH 3 EVIDENCE/PRECISION GAPS. Core technical premises verified TRUE: (1) search_references exists on main with the exact AC2 surface (100 cap, disclaimer, 3 usage filters, kind filter) and AC3 error paths; (2) RuntimeSupervisor + lgrep_diagnostics + JobStatus dispositions exist, so AC4 is achievable, and config caps workers at 2 (5 concurrent -> 2 run / 3 queue); (3) vision_restart lgrep is a real per-server restart command, so the cutover explicit-restart step (C2) is executable. Contract preservation GOOD: all 4 constraints and 3 OOS honored; no implementation folded in. THREE issues would make AC1/AC3/AC5 unverifiable AS WRITTEN: (a) AC3 'no request exceeds 8 seconds' stale vs live LGREP_TOOL_TIMEOUT_S=30; (b) AC5 rollback prior-artifact SOURCE unspecified, no PyPI publish path; (c) AC1 'released version' wording conflicts with deploying unreleased 3.2.0 HEAD. None reject the design; all fixable in revision before planning.
- **[unresolved_action]** validation.blockers: [HIGH] AC3 'no request exceeds 8 seconds' is STALE vs live config. servers.yaml L57 sets LGREP_TOOL_TIMEOUT_S=30; the 8s figure traces only to CHANGELOG v3.1.3 (L37) and repo-improve-prep operator-ticket notes for v3.1.0 -- historical, not current. Design hardcodes an unsourced '8-second service budget'.
- **[unresolved_action]** validation.blockers: [HIGH] Rollback prior-artifact SOURCE unspecified; no PyPI publish path. pyproject has no PyPI metadata; no .github/workflows release pipeline. lgrep installed via uv-tool from local checkout, so prior artifact (reportedly 3.1.3) only recoverable via git ref/tag or cached wheel. Design step 6 says 'reinstall the recorded prior revision' but names neither ref nor command.
- **[unresolved_action]** validation.blockers: [MEDIUM] 'released version' wording vs unreleased 3.2.0 HEAD deployment. CHANGELOG lists lgrep_search_references under '## Unreleased' (L5); pyproject=3.2.0 with no release tag. C1 authorizes deploying from main, but AC1 'reports the released version' is in tension with shipping an unreleased HEAD build to the shared Vision service.

## Contract / AC Coverage

| ID | Kind | Status |
| --- | --- | --- |
| AC1 | acceptance_criterion | pass |
| AC2 | acceptance_criterion | pass |
| AC3 | acceptance_criterion | pass |
| AC4 | acceptance_criterion | pass |
| AC5 | acceptance_criterion | pass |
| AC6 | acceptance_criterion | pass |
| C1 | constraint | respected |
| C2 | constraint | respected |
| C3 | constraint | respected |
| C4 | constraint | respected |
| OOS1 | out_of_scope | not_applicable |
| OOS2 | out_of_scope | not_applicable |
| OOS3 | out_of_scope | not_applicable |

## Unresolved Actions

- [HIGH] AC3 'no request exceeds 8 seconds' is STALE vs live config. servers.yaml L57 sets LGREP_TOOL_TIMEOUT_S=30; the 8s figure traces only to CHANGELOG v3.1.3 (L37) and repo-improve-prep operator-ticket notes for v3.1.0 -- historical, not current. Design hardcodes an unsourced '8-second service budget'.
- [HIGH] Rollback prior-artifact SOURCE unspecified; no PyPI publish path. pyproject has no PyPI metadata; no .github/workflows release pipeline. lgrep installed via uv-tool from local checkout, so prior artifact (reportedly 3.1.3) only recoverable via git ref/tag or cached wheel. Design step 6 says 'reinstall the recorded prior revision' but names neither ref nor command.
- [MEDIUM] 'released version' wording vs unreleased 3.2.0 HEAD deployment. CHANGELOG lists lgrep_search_references under '## Unreleased' (L5); pyproject=3.2.0 with no release tag. C1 authorizes deploying from main, but AC1 'reports the released version' is in tension with shipping an unreleased HEAD build to the shared Vision service.
