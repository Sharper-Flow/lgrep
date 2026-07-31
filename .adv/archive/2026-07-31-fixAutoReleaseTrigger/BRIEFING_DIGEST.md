# Archive Briefing Digest

**Change ID:** fixAutoReleaseTrigger
**Title:** Fix auto release trigger
**Status:** archived
**Generated:** 2026-07-31T20:49:29.736Z

## Identity Anchors

- CHANGE
- STATUS
- TERMINAL_GATE_SUMMARY
- Origin: discovery

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

Showing 52 of 52 durable facts.

- **[archive_only_evidence]** decisions: Removed in-workflow CHANGELOG commit/pushback — Eliminates detached-HEAD pushback race so the release tag stays on the exact triggering SHA and artifact version matches source.
- **[archive_only_evidence]** decisions: Added explicit head_branch == 'main' and event == 'push' guards to the release job — Ensures only successful main-branch push CI runs select the release job, preventing zero-job or wrong-branch release attempts.
- **[archive_only_evidence]** decisions: Checked out github.event.workflow_run.head_sha and tag before build — Guarantees the tag, source checkout, and Hatch VCS-derived artifact version are all identical.
- **[archive_only_evidence]** decisions: Added pyyaml to dev dependencies — Supports deterministic static parsing of the release workflow in the new regression tests.
- **[archive_only_evidence]** verification: python -m pytest tests/test_auto_release.py -v (1) — Durable red test: workflow YAML parse/structure failed before fix
- **[archive_only_evidence]** verification: python -m pytest tests/test_auto_release.py -v (0) — Green: 9 auto-release static/fixture tests pass after workflow fix
- **[archive_only_evidence]** verification: python -m pytest tests/test_version.py tests/test_auto_release.py -v (0) — Verify: version tests plus new auto-release tests pass
- **[archive_only_evidence]** verification: python -m pytest tests/test_auto_release.py -v && python -m ruff check tests/test_auto_release.py (0) — Final verify: auto-release tests and lint pass
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9c9jjb_28a6fbaa
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9ccua1_637a5c57
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9cd76e_8c83176a
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9ceill_c2ddfa55
- **[unresolved_action]** required_main_agent_actions: Review scoped worktree edits and create the required task checkpoint; no further implementation changes are required.
- **[unresolved_action]** required_main_agent_actions: Leave deploy command and unrelated workflow behavior untouched.
- **[wisdom_candidate]** wisdom_candidates: [pattern] Post-release documentation pushbacks should be a final continue-on-error step on a fresh default-branch checkout; tag/build/release must remain bound to the triggering SHA.
- **[archive_only_evidence]** changes_made: .github/workflows/auto-release.yml: Added final Update CHANGELOG step after release creation. It switches to an origin/main tracking checkout, generates and commits CHANGELOG.md, and pushes to main under continue-on-error so documentation failure cannot block tag, build, or release creation.
- **[archive_only_evidence]** changes_made: tests/test_auto_release.py: Replaced the blanket no-pushback assertion with static invariants allowing only the isolated final changelog update to commit and push, while prohibiting all other detached-HEAD pushbacks.
- **[archive_only_evidence]** verification: tests_run=python -m pytest tests/test_auto_release.py, ruff check ., python -m pytest, git diff --check results=pass — Targeted workflow suite: 10 passed. Ruff: all checks passed. Full suite: 800 passed in 91.15s (17 existing deprecation warnings). git diff --check passed.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_auto_release.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: ruff check .
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
- **[archive_only_evidence]** decisions: Implemented deploy command as a standalone script in scripts/deploy_vision.py instead of a package CLI subcommand — Keeps deployment tooling repo-maintenance scoped and avoids adding runtime dependencies to the lgrep package
- **[archive_only_evidence]** decisions: Implemented a minimal MCP Streamable HTTP client using urllib instead of relying on the mcp SDK — Avoids new dependencies and keeps the script self-contained for repository maintenance use
- **[archive_only_evidence]** decisions: Used dry_run=true and a string refused_reason as health evidence — Matches the acceptance criterion that health checks be non-destructive and return structured preview/refusal results
- **[archive_only_evidence]** verification: python -m pytest tests/test_deploy_vision.py -v (0) — 23 deploy command tests pass (safety, retry, version, MCP health)
- **[archive_only_evidence]** verification: python -m pytest -q (0) — Full suite: 797 passed, 17 warnings
- **[archive_only_evidence]** verification: python -m ruff check scripts/deploy_vision.py tests/test_deploy_vision.py && python -m ruff format --check scripts/deploy_vision.py tests/test_deploy_vision.py (0) — Ruff lint and format clean for new files
- **[archive_only_evidence]** verification: python scripts/deploy_vision.py --dry-run --tag v3.2.5 (1) — Correctly refuses to run from worktree branch change/fixAutoReleaseTrigger
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9d6ouf_be2545a7
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9d93i7_13d15f06
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: local-ruff-20260731
- **[unresolved_action]** consumer_warnings: verification_missing: No durable adv_run_test evidence found for run_id: local-dryrun-20260731
- **[report_follow_up]** follow_ups: Scope-key note: the packet supplied SCOPE KEY 'design:release-local-deploy' but the typed report schema requires the researcher: prefix, so scope_key was normalized to 'researcher:release-local-deploy'.
- **[report_follow_up]** follow_ups: Confirm 'no-guess-dev' is accepted by the installed hatch-vcs/setuptools_scm version at build time (the proposed AC2 wheel-metadata fixture is the authoritative check; flag only if the fixture fails).
- **[report_follow_up]** follow_ups: Confirm the systemd user-unit name / restart command for Vision (design says vision.service) before execution.
- **[report_follow_up]** follow_ups: Decide whether the simpler in-ci.yml release-job alternative (push event, needs:[lint,test]) is worth the churn vs keeping the corrected workflow_run approach — conscious choice, not blocking.
- **[research_citation]** sources: GitHub Actions — Events that trigger workflows (workflow_run: GITHUB_SHA = last commit on default branch): workflow_run sets GITHUB_SHA to the LAST COMMIT ON THE DEFAULT BRANCH and GITHUB_REF to the default branch — NOT the commit CI validated. conclusion-based gating via github.event.workflow_run.conclusion is documented. This is the root of the SHA hazard the design fixes. (https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_run)
- **[research_citation]** sources: actions/checkout README — ref default (event ref/SHA else default branch): ref: 'When checking out the repository that triggered a workflow, this defaults to the reference or SHA for that event. Otherwise, uses the default branch.' For workflow_run that resolves to default-branch HEAD. Design's explicit ref: github.event.workflow_run.head_sha is the correct fix. (https://github.com/actions/checkout)
- **[research_citation]** sources: actions/checkout input-helper.ts — default ref resolution falls back to github.context.ref/sha: When no explicit ref and target is the workflow repo, sets result.ref=github.context.ref, result.commit=github.context.sha. For workflow_run these are default-branch values. (https://github.com/actions/checkout/blob/main/src/input-helper.ts)
- **[research_citation]** sources.omitted: 11 additional sources omitted (bounded to first 3)
- **[archive_only_evidence]** architecture_assessment: Core design is correct and well-sourced. (1) workflow_run + actions/checkout-without-ref checks out DEFAULT-BRANCH HEAD, not the CI-validated commit — verified against the GitHub events doc (workflow_run GITHUB_SHA = 'last commit on default branch'), actions/checkout source (input-helper.ts falls back to github.context.sha), a real-world identical bug (Sawtaytoes/mux-magic), and the current auto-release.yml (no ref). The design's ref: github.event.workflow_run.head_sha is the canonical fix. (2) The design's conclusion/head_branch/event checks all reference valid github.event.workflow_run.* properties (confirmed by actions/checkout unsafe-pr-checkout-helper.ts reading workflow_run.event/head_sha/head_branch/head_commit). (3) Tag-before-build + hatch-vcs no-guess-dev yields exactly X.Y.Z at the tagged SHA (pyproject.toml). (4) The Vision runtime is /home/jon/.local/bin/lgrep (servers.yaml), so a uv-tool install/upgrade is the right deploy mechanism; health proof via two dry_run=True prune previews is genuinely non-destructive (C3) and the MCP contract carries refused_reason as a non-nullable string ('' on success). Three correctness-affecting clarifications remain before the design is airtight.
- **[unresolved_action]** required_main_agent_actions: Collect the agreement-required live evidence after a tagged release: run the documented command from clean merged main and record Vision version plus both non-destructive MCP results.
- **[unresolved_action]** required_main_agent_actions: Inspect the next successful main CI workflow_run for a selectable Auto Release job and published tag-matched wheel/sdist assets.
- **[wisdom_candidate]** wisdom_candidates: [gotcha] Streamable HTTP MCP clients must handle both application/json JSON-RPC responses and text/event-stream SSE responses; selecting application/json in Accept while parsing only SSE makes local health verification fail against compliant JSON responders.
- **[archive_only_evidence]** changes_made: scripts/deploy_vision.py: Handle application/json JSON-RPC responses as well as SSE responses in the Vision MCP client; Streamable HTTP servers may return either form.
- **[archive_only_evidence]** changes_made: tests/test_deploy_vision.py: Add a regression test for an application/json MCP response so the client cannot regress to SSE-only parsing.
- **[archive_only_evidence]** verification: tests_run=python -m pytest tests/test_auto_release.py tests/test_deploy_vision.py -q, python -m ruff check scripts/deploy_vision.py tests/test_deploy_vision.py, python -m ruff format --check scripts/deploy_vision.py tests/test_deploy_vision.py, git diff --check results=pass — 33 targeted tests passed in 0.37s; Ruff check reported all checks passed; Ruff format reported both files already formatted; git diff --check emitted no errors. Reviewed workflow checks out github.event.workflow_run.head_sha, tags before build, and only pushes the tag.
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_auto_release.py tests/test_deploy_vision.py -q
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check scripts/deploy_vision.py tests/test_deploy_vision.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check scripts/deploy_vision.py tests/test_deploy_vision.py
- **[unresolved_action]** consumer_warnings: verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check

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

## Unresolved Actions

- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9c9jjb_28a6fbaa
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9ccua1_637a5c57
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9cd76e_8c83176a
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9ceill_c2ddfa55
- Review scoped worktree edits and create the required task checkpoint; no further implementation changes are required.
- Leave deploy command and unrelated workflow behavior untouched.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_auto_release.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: ruff check .
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9d6ouf_be2545a7
- verification_missing: No durable adv_run_test evidence found for run_id: tr_ms9d93i7_13d15f06
- verification_missing: No durable adv_run_test evidence found for run_id: local-ruff-20260731
- verification_missing: No durable adv_run_test evidence found for run_id: local-dryrun-20260731
- Collect the agreement-required live evidence after a tagged release: run the documented command from clean merged main and record Vision version plus both non-destructive MCP results.
- Inspect the next successful main CI workflow_run for a selectable Auto Release job and published tag-matched wheel/sdist assets.
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m pytest tests/test_auto_release.py tests/test_deploy_vision.py -q
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff check scripts/deploy_vision.py tests/test_deploy_vision.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: python -m ruff format --check scripts/deploy_vision.py tests/test_deploy_vision.py
- verification_missing: Reviewer aggregate evidence is non-authoritative; no typed adv_run_test run ID proves command: git diff --check
