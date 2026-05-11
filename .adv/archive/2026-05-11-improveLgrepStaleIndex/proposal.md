# Improve lgrep stale-index resilience

## Summary
Agents report stale lgrep indexes causing poor or misleading code discovery. Improve lgrep so agents can detect, avoid, and repair stale semantic/symbol indexes without manual guesswork.

## Scope
### In Scope
- Semantic index freshness diagnostics for one project and all projects.
- Schema-safe `lgrep_status_semantic(path="")` response.
- Symbol incremental indexing cleanup for deleted files.
- Auto-staleness check in `search_semantic` with automatic re-index.
- Better default ignore guidance to reduce stale ADV proposal/archive noise.
- Agent-facing instructions for stale-index recovery.

### Out of Scope
- Replacing LanceDB or symbol storage backend.
- Network/security model changes for MCP transport.
- Repo-wide ranking redesign.
- Automatic background daemon installation changes beyond documented guidance.

## Success Criteria
- Agents can determine whether semantic index data is fresh enough without hitting response validation errors.
- Incremental symbol re-index no longer keeps symbols for deleted files.
- `search_semantic` auto-detects stale indexes and re-indexes transparently.
- Default/recommended ignore patterns reduce indexing of stale ADV change/archive state while preserving useful specs.
- Tests prove stale/deleted-file behavior and status response shape.
- Repo verification passes.