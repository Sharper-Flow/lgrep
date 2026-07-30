# Executive Summary — Gate remaining destructive MCP tools

## Outcome

Every MCP tool that declares itself destructive now requires the same explicit server-side `LGREP_ALLOW_DESTRUCTIVE_MCP=1` grant before it can delete data. The v3.2.2 security fix had correctly gated the two prune tools but missed two adjacent delete paths: symbol-index invalidation and worktree-cache invalidation.

Without the grant, prune tools return a preview. The invalidation tools have no preview parameter, so they safely do nothing and return a schema-compatible `refused_reason`. Their message tells the operator exactly what is missing and truthfully says there is no CLI equivalent.

## Value

A shared Vision proxy can front a local subprocess and still report `stdio`, so connection type does not establish caller authority. The change makes explicit grant — not a proxy-shaped inference — the complete invariant across the four destructive MCP tools.

The invariant is structural now: tests enumerate the MCP registry and pin exactly four `destructiveHint=True` tools. Adding or omitting a destructive tool forces an explicit test review rather than silently creating another exception.

## Verification

- Without grant: all four tools prove no deletion.
- With grant: all four prove actual deletion in isolated temporary fixtures only.
- `DiagnosticsResult.transport` remains available, but transport no longer flows through a process-wide environment variable that could be mistaken for authorization input.
- Full suite: 736 passed. Lint and formatting clean.
- Review strengthened proof that refused index invalidation leaves an existing index file intact, while the granted call deletes that exact file.

## Risk and release proof

`invalidate_worktree_cache` refuses the whole operation without the grant, including alias cleanup; this is the agreed safe no-op behavior rather than a partial mutation.

The final live proof is release-time: after merged-trunk deployment, invoke both invalidation tools through the shared Vision endpoint without the grant and confirm refusal plus service health. The current shared service remains unchanged until then.