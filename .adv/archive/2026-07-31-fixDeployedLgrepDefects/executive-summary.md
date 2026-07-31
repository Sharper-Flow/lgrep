# Executive Summary: Fix deployed lgrep defects

## Outcome

lgrep maintenance tools now return valid structured MCP responses, releases derive their package version from the release tag, and large repositories index in bounded windows until complete.

## Value

Users can run maintenance previews again, identify deployed releases consistently, and avoid silently incomplete semantic indexes on large repositories.

## Verification

- Independent acceptance review added timeout/cancellation schema coverage.
- 761 tests passed.
- Ruff lint and formatting checks passed.
- CLI version matches tag-derived `3.2.3` in review evidence.

## Risks and follow-up

No deployment was run from the worktree. Release/deployment verification remains required after merge from trunk.