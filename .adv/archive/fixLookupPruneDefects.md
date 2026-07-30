# fixLookupPruneDefects: Fix lookup and prune defects

**Status:** archived
**Branch:** main (merged at e735ac2669dc8f09a62bf9e48c7cd3be7ba9b5c2)
**Timeline:** 2026-07-30T03:14:25.215Z → 2026-07-30T04:34:57.608Z

## Outcome
Completed 4 task(s): Require an explicit grant for destructive MCP maintenance; Make usage filters distinct and truncation visible; Signal staleness on returned occurrences; Re-pro…

## Why
Managed-endpoint validation of lgrep found three defects that reach agents directly. Destructive prune tools are reachable without their intended guard because…

## Surface
- Require an explicit grant for destructive MCP maintenance
- Make usage filters distinct and truncation visible
- Signal staleness on returned occurrences
- Re-prove the three fixes through the managed endpoint

## Acceptance Criteria
- ✓ **AC1 — Destructive rights require explicit grant:** transport kind alone never authorizes destructive maintenance thro…
- ✓ **AC2 — Opt-in and CLI still work:** with the explicit opt-in present, destructive MCP calls perform real work, and the…
- ✓ **AC3 — Filters are observably distinct:** for one query over a corpus whose test occurrences fall beyond the result ca…
- ✓ **AC4 — Truncation is visible:** responses report how many production and test occurrences matched and how many were re…
- ✓ **AC5 — Staleness is signalled:** when a returned occurrence comes from a file whose current content no longer matches …
- ✓ **AC6 — Contract and budget preserved:** results remain explicitly candidate-only, and a capped query against a fixture…
- ✓ **AC7 — Proven end to end:** the full test suite and lint pass, and the three fixed behaviors are reproduced through th…

## Spec Deltas
- lgrep-daemon-operational-safety/dl-destructiveGrant02: add
- lgrep-candidate-reference-lookup/dl-lookupHonesty01: add

## Wisdom Promoted
- None

## Approval
archive-projection-reconciler, Phase 9 finalization shipped; defaultBranch=main; mainCheckout=/home/jon/dev/lgrep; pushStatus=

<!-- summary truncated to stay under 2KB -->
