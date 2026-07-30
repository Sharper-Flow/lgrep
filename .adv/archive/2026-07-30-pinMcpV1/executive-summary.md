# Executive Summary

## Outcome

lgrep installs work again. The package now holds the MCP SDK on the v1 line, and two guards make the same silent break impossible to reintroduce unnoticed.

## Value

Before this change, every fresh install of lgrep produced a server that could not start and registered no tools at all, so agents lost the entire code-search surface. The candidate reference lookup shipped in the previous release had, in fact, never worked through the tool interface even once.

## What went wrong

The package asked for any MCP SDK from 1.0 upward. When version 2.0 became the stable release, fresh installs began picking it up, and version 2 removed the module the server depends on. Nothing warned about this, because the tests that would have caught it could not even be loaded in that broken state.

Unblocking those tests immediately exposed a second, independent defect: the reference-lookup tool passed a filter argument whose name collided with an internal scheduling parameter, so every call through the tool interface failed before doing any work.

## Verification

- Full test suite: 698 passed, linting clean. This is the first complete suite run this repository has had.
- A guard was proven to fail against the original unbounded declaration before the fix, then pass after it.
- The bound guard was checked against every regression form and every valid bound form, so it cannot produce false alarms.
- A clean throwaway build resolved a supported SDK version, started successfully, and advertised all 21 tools including reference lookup.

## Risks and follow-ups

- The shared Vision service still runs the previous restored version. Redeploying and validating it belongs to the parent validation change.
- Version 3.2.0 remains published and is unusable wherever the newer SDK resolves. The changelog states this plainly.
- The upgrade to MCP SDK v2 is deliberately deferred. Independent research confirmed Vision needs no change to host such a server, so that migration can proceed from this stable baseline.
- Other runtime dependencies remain unbounded and there is no lockfile. That broader hardening is not addressed here.