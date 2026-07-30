# Executive Summary — Vision lgrep integration validation

## Outcome

The shared lgrep service was cut over to a released artifact and then genuinely stress-tested through its real managed endpoint rather than through the convenient path. It worked, and more importantly it found things.

The validation proved the managed service exposes and correctly executes reference lookup: candidate framing, ordering, filters, the result cap, bounded failure handling for malformed and impossible requests, and five simultaneous requests completing without runaway workers. The slowest request across the entire matrix was 0.038 seconds against an eight-second budget.

## Why it matters

This change is best judged by what it caught, not by whether everything passed. It found five durable problems, three of them real defects in shipped code:

- **A security exposure.** The guard preventing deletion through the shared service decided authority by looking at the connection type, treating a local-looking connection as a trusted single user. The shared proxy invalidates that assumption: it runs the service as a local subprocess but republishes it on an unauthenticated port, so the connection still looks local no matter who is calling. Anyone reaching the port could delete indexed data.
- **A filter that did nothing.** Asking to include test occurrences returned none, because production occurrences filled the result cap before any test occurrence was considered. The option existed and had no observable effect.
- **Results that lied about their freshness.** Occurrences from files edited since indexing came back with stale line numbers and text, with nothing to distinguish them from current results.

All three were fixed and released as v3.2.2, which is now deployed and independently re-verified through the same managed endpoint.

Two further lessons were recorded. One is a method: validate a managed service through its real endpoint, because two false conclusions were only avoided that way — an apparent outage that was actually a missing request header, and an apparently missing tool namespace that was actually a client-side cache. The other is a warning: an unbounded dependency had made an entire test suite uncollectable, that failure had been repeatedly waved through as environmental, and it was concealing a shipped feature that had never once executed end to end.

## Verification

- Managed matrix executed against the real proxied endpoint using isolated temporary fixtures, never real repository data.
- Rollback was proven rather than rehearsed: the first cutover failed, was rolled back to the prior artifact, and the service was verified healthy before redeploying a corrected release.
- Re-verified today against the currently deployed artifact: all behaviors pass, slowest request 0.034 seconds.
- Contract review: 13 of 13 items satisfied, none failing.

## An honest note on the criterion that failed

The core-behavior criterion did not pass when it was first measured. On the artifact this change deployed, the test-inclusion filter was indistinguishable from the default, so the distinct behavior the criterion required was never observed. It is recorded here as satisfied because it has now been demonstrated against the artifact currently in service, following the fix that this very finding produced.

The original failure has been left intact in the task record, in the recorded findings, and in the remediation change. It is not being reinterpreted. A validation exercise that surfaces a real defect has done its job; the criterion is marked satisfied because the service now behaves correctly, not because the earlier result was reconsidered.

## Risks and follow-ups

- Load behavior was validated at five concurrent requests. Because each completes in about thirty milliseconds, queueing and rejection paths could not be triggered, so saturation behavior remains unproven.
- The follow-on review found that the deletion guard was extended to only two of the four tools that actually delete. That gap is tracked separately and is not closed by this change.
- Client sessions cache the tool catalog at startup, so any restart of the shared service leaves existing sessions reporting the tools as missing until they restart. This is expected and documented, not a fault.
