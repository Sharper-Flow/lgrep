# Executive Summary — Fix flaky latency benchmarks

## Outcome

CI no longer lets one scheduler pause masquerade as a product regression. The two latency tests had calculated `int(10 * 0.95)`, which selected the maximum sample while claiming to report p95. One 109ms or 159ms shared-runner stall could red-line a search operation whose normal time is roughly 0.3ms.

Both benchmarks now use Python's standard `statistics.median` across the existing ten calls. The actual regression budgets remain strict: 100ms for local semantic search and 50ms for symbol search. A separate strict 1000ms per-call check catches hangs and deadlocks without pretending to be tail-latency coverage.

## Value

Unrelated changes stop failing CI because another process briefly received CPU time. At the same time, a uniform slowdown still fails: ten 60ms symbol-search samples produce a 60ms median and breach the unchanged 50ms budget.

## Verification

- Pure tests prove one 159ms outlier is ignored, uniform slow behavior fails, even-count median behavior is correct, both budgets have strict boundaries, and the hang guard is strict.
- Focused suite: 9 passed.
- Independent verification: 724 full-suite tests passed; lint and formatting clean.
- Review found and fixed missing pure boundary coverage for the semantic 100ms budget.

## Risk

This is intentionally not a tail-latency SLA; shared-runner unit tests cannot establish one. The hang guard still catches pathological blocking. Post-merge CI across Python 3.11/3.12/3.13 is release evidence.