# Executive Summary — Fix lookup and prune defects

## Outcome

Three defects found by managed-endpoint validation of the previous change are fixed and proven over the real MCP protocol.

**Deleting through the shared service now requires an explicit grant.** The two destructive maintenance tools decided whether a caller was allowed to delete by looking at the transport kind, treating `stdio` as proof of a single trusted local user. That inference was wrong in exactly the deployment that most needed protection: the shared service runs lgrep as a local subprocess but republishes it on an unauthenticated port, and the subprocess still reports `stdio` — so any client that could reach the port could delete indexed data. Authority is now an explicit server-side opt-in that defaults to off. Without it the tools return a preview and state which grant or which command-line equivalent would be needed. The command-line path, whose caller already holds local shell authority, is unchanged.

**The reference-lookup filters now mean what their names say.** Asking to include test occurrences returned none, because production occurrences alone filled the result cap before any test occurrence was considered. A fifth of the cap is now held for test occurrences, with unused capacity handed back to the other group, so the three filter modes produce three genuinely different result sets.

**Truncated and stale results now say so.** Responses report how many production and test occurrences matched versus how many were returned, so a caller can tell results were cut off instead of inferring it. Each result is marked stale when its source file has changed since indexing, with a per-response count of stale files; a deleted file is reported as stale rather than raising an error. Freshness reuses a fingerprint the index already stores, so no re-reading of the repository is involved and the check costs nothing beyond the rows actually returned.

## Why it matters

The first fix closes a data-loss exposure on a shared service. The other two close a subtler failure: the tool was answering confidently with results that were incomplete or out of date, and gave the caller no way to notice. Silent wrongness is more expensive than visible wrongness, because it is acted on.

## Verification

- Full test suite: **719 passed**, lint clean.
- All four tasks followed a failing-test-first cycle with recorded red and green runs.
- Ten behavioural scenarios exercised over real JSON-RPC against a clean build of the change branch: **all pass**, slowest request 0.023s against an 8-second budget.
- Independent review across security, correctness, and contract traceability. Contract coverage 100% — all 14 items traced, every diff hunk attributable.

## Risks and follow-ups

- **Managed re-verification is a post-merge step.** The shared service must run an artifact built from merged trunk, so pre-merge proof was taken over the same local subprocess channel the service itself spawns. Re-running the scenario matrix through the managed endpoint after merge is the confirming step.
- **The prior change remains parked at acceptance** with one criterion recorded as failed — the very defects this change fixes. It needs a decision once this merges.
- The grant is a capability flag, not caller authentication. Anyone who can set the service's environment can enable deletion. Authenticating the proxy itself was explicitly out of scope.

## Review findings

Nine findings, no blockers; six fixes applied during remediation. The substantive one: the staleness annotation was writing its verdicts into the shared cached index rather than into copies, which would have leaked read-only query state into indexed data on the next save — a violation of this change's own scope boundary that the original tests did not catch. Also fixed: the freshness read was not confined to the repository root and could follow an absolute path outside it; a malformed path would have raised an uncaught error type and failed the whole lookup; and two specification documents still mandated the transport rule this change removes.
