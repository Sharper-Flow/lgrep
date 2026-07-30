# pinMcpV1: Pin mcp to v1

**Status:** archived
**Branch:** main (merged at 9d9ac144e3a5fcbed699547309578c2a6340fd58)
**Timeline:** 2026-07-30T02:21:48.325Z → 2026-07-30T03:01:47.377Z

## Outcome
Completed 3 task(s): Bound the MCP SDK and add registration guards; Prove a freshly built installation serves the full tool surface; Record the stability release

## Why
Every fresh lgrep installation is broken. The package declares an unbounded `mcp>=1.0.0`, so resolution now selects the newly stable `mcp` 2.0.0, which removed…

## Surface
- Bound the MCP SDK and add registration guards
- Prove a freshly built installation serves the full tool surface
- Record the stability release

## Acceptance Criteria
- ✓ **AC1 — Bounded dependency:** the runtime dependency on the MCP SDK carries an explicit upper bound excluding 2.x, matc…
- ✓ **AC2 — Registration guard:** an automated test fails when the resolved MCP SDK cannot support the server, and fails wh…
- ✓ **AC3 — Bound guard:** an automated check fails if the declared dependency bound is removed or widened to admit 2.x.
- ✓ **AC4 — Suites restored:** the server and CLI test suites that previously could not be collected now collect and pass.
- ✓ **AC5 — Installable artifact:** a freshly built installation resolves an MCP SDK below 2.0 and, when started, advertise…
- ✓ **AC6 — Release record:** the package version and changelog record this as a stability fix, and the release is tagged.

## Spec Deltas
- lgrep-dependency-integrity/dl-mcpSdkBound01: add

## Wisdom Promoted
- None

## Approval
archive-projection-reconciler, Phase 9 finalization shipped; defaultBranch=main; mainCheckout=/home/jon/dev/lgrep; pushStatus=pushed; releasedCommitSh…, 2026-07-30T03:01:47.377Z
