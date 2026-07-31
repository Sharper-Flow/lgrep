# Executive Summary: Fix auto release trigger

## Outcome

lgrep releases now trigger deterministically from successful main CI, and operators have one safe local deployment command with real Vision health proof.

## Why It Matters

Eliminates the zero-job release failure that forced manual v3.2.4 publication. Operators can update Vision and verify health without manual recovery steps.

## Verdict

APPROVED

## What Was Built

1. Auto Release workflow now checks out the exact triggering CI SHA, tags before building, and uses correct GitHub Actions YAML (block scalar for release files).
2. Added a safe Vision deploy command (`scripts/deploy_vision.py`) that refuses non-trunk/dirty source, installs tagged release wheels, restarts Vision, verifies CLI version, and runs non-destructive MCP health checks with bounded retry.
3. CHANGELOG generation restored as a post-release best-effort step from origin/main.

## What Was Verified

- Verdict: APPROVED with 3 blockers found and fixed by reviewer (workflow YAML regression, test shape assertion, MCP Accept header)
- Tests: 800 passed, Ruff clean
- Preview URL: not_applicable — backend automation and CLI tooling
- Contract matrix: 6 AC pass, 3 constraints respected
- Live Vision prune calls verified by reviewer with corrected Accept header

## Remaining Concerns

- AC1/AC2 empirical proof requires a real post-merge release run; static and schema validation is the pre-merge ceiling.
- AC3/AC5 deploy command unit tests are mock-based; real deployment verification occurs post-merge.
- `action-gh-release@v2` uses deprecated Node 20 runner — non-blocking follow-up.

## Supporting Evidence

- Task tk-211aea8c7d3d: workflow YAML fix + fixture tests (10 tests)
- Task tk-5ad7d7b8cd00: deploy command + 23 unit tests + docs
- Reviewer report: 3 blockers fixed, live Vision verification, 800 tests
- Checkpoint SHA: 76f010cfd36608823faad1adac9ba32e9ded2bef