"""lgrep_search_references tool implementation.

Bounded, read-only candidate reference lookup for indexed Python repositories.
Returns occurrence locations with enclosing context and explicit candidate/
non-exhaustive disclaimers. Supports production-first and test filtering.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

if TYPE_CHECKING:
    from pathlib import Path


_USAGE_FILTERS = frozenset({"production_first", "include_tests", "tests_only"})
_OCCURRENCE_KINDS = frozenset({"call", "attribute", "import", "reference"})
MAX_REFERENCE_RESULTS = 100

# Share of the result cap held back for test occurrences under "include_tests".
# Without a reserve, production occurrences alone fill the cap in any real
# repository and the filter has no observable effect. Unused reserve is
# returned to production, so the cap is never left partly empty.
TEST_RESERVE_RATIO = 0.2

_DISCLAIMER = (
    "Candidate occurrences only; results are not compiler-accurate or exhaustive."
)


def search_references(
    query: str,
    repo_path: str,
    storage_dir: Path | str | None = None,
    limit: int = 20,
    usage_filter: str = "production_first",
    kind: str | None = None,
) -> dict:
    """Search for candidate references/uses of a symbol name in an indexed repo.

    Args:
        query: Symbol name to look up (e.g. "authenticate").
        repo_path: Absolute path to the indexed repository root.
        storage_dir: Optional override for the symbol index storage directory.
        limit: Maximum number of occurrence results to return (default: 20,
            capped at 100).
        usage_filter: One of "production_first", "include_tests", "tests_only".
        kind: Optional occurrence kind filter ("call", "attribute", "import",
            "reference").

    Returns:
        Dict with query, usage_filter, total_matches, results list,
        candidate_names, disclaimer, and _meta envelope.
        Returns an error dict for invalid input or missing/stale index.
    """
    t0 = time.monotonic()

    if not query or not query.strip():
        return error_response("query must not be empty", _meta=make_meta(t0))
    query = query.strip()

    if usage_filter not in _USAGE_FILTERS:
        return error_response(
            f"usage_filter must be one of {sorted(_USAGE_FILTERS)}; got {usage_filter!r}",
            _meta=make_meta(t0),
        )

    if kind is not None and kind not in _OCCURRENCE_KINDS:
        return error_response(
            f"kind must be one of {sorted(_OCCURRENCE_KINDS)}; got {kind!r}",
            _meta=make_meta(t0),
        )

    if limit < 1:
        limit = 1
    limit = min(limit, MAX_REFERENCE_RESULTS)

    store = IndexStore(storage_dir=storage_dir)
    repo_key = normalize_repo_key(repo_path)
    index = store.load(repo_key)
    if index is None:
        return error_response(
            f"Repository not indexed: {repo_path}. Run lgrep_index_symbols_folder first.",
            _meta=make_meta(t0),
        )

    # Stale/invalid index: occurrence data is required for this tool.
    if not index.occurrences:
        return error_response(
            f"Symbol index is missing candidate occurrence data for {repo_path}. "
            "Run lgrep_index_symbols_folder to refresh.",
            _meta=make_meta(t0),
        )

    query_lower = query.lower()
    candidate_names = sorted(
        name for name in index.occurrences if name.lower() == query_lower
    )

    if not candidate_names:
        return {
            "query": query,
            "usage_filter": usage_filter,
            "total_matches": 0,
            "production_matches": 0,
            "test_matches": 0,
            "returned_production": 0,
            "returned_tests": 0,
            "results": [],
            "candidate_names": [],
            "disclaimer": _DISCLAIMER,
            "_meta": make_meta(t0, tokens_saved=estimate_savings(0)),
        }

    matches: list[dict] = []
    for name in candidate_names:
        matches.extend(index.occurrences[name])

    if kind is not None:
        matches = [m for m in matches if m.get("kind") == kind]

    def _position(m: dict) -> tuple:
        return (m.get("file_path", ""), m.get("line_number", 0))

    production = sorted((m for m in matches if not m.get("is_test_file")), key=_position)
    tests = sorted((m for m in matches if m.get("is_test_file")), key=_position)

    if usage_filter == "tests_only":
        matches = tests
        results = tests[:limit]
    elif usage_filter == "include_tests":
        reserve = min(len(tests), int(limit * TEST_RESERVE_RATIO)) if tests else 0
        if tests and reserve == 0:
            reserve = min(len(tests), 1)

        chosen_production = production[: max(limit - reserve, 0)]
        chosen_tests = tests[:reserve]

        # Hand any unused capacity back to the other group so the cap stays full.
        spare = limit - len(chosen_production) - len(chosen_tests)
        if spare > 0:
            extra_tests = tests[len(chosen_tests) : len(chosen_tests) + spare]
            chosen_tests += extra_tests
            spare -= len(extra_tests)
        if spare > 0:
            chosen_production += production[
                len(chosen_production) : len(chosen_production) + spare
            ]

        results = chosen_production + chosen_tests
    else:
        # "production_first" — production occurrences first, tests after.
        matches = production + tests
        results = matches[:limit]

    total_matches = len(matches)
    returned_tests = sum(1 for m in results if m.get("is_test_file"))

    tokens_saved = estimate_savings(len(results))
    return {
        "query": query,
        "usage_filter": usage_filter,
        "total_matches": total_matches,
        "production_matches": len(production),
        "test_matches": len(tests),
        "returned_production": len(results) - returned_tests,
        "returned_tests": returned_tests,
        "results": results,
        "candidate_names": candidate_names,
        "disclaimer": _DISCLAIMER,
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
