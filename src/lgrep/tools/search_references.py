"""lgrep_search_references tool implementation.

Bounded, read-only candidate reference lookup for indexed Python repositories.
Returns occurrence locations with enclosing context and explicit candidate/
non-exhaustive disclaimers. Supports production-first and test filtering.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from lgrep.discovery import MAX_FILE_SIZE_BYTES
from lgrep.storage.index_store import IndexStore, normalize_repo_key
from lgrep.storage.token_tracker import estimate_savings
from lgrep.tools._meta import error_response, make_meta

_USAGE_FILTERS = frozenset({"production_first", "include_tests", "tests_only"})
_OCCURRENCE_KINDS = frozenset({"call", "attribute", "import", "reference"})
MAX_REFERENCE_RESULTS = 100

# Share of the result cap held back for test occurrences under "include_tests".
# Without a reserve, production occurrences alone fill the cap in any real
# repository and the filter has no observable effect. Unused reserve is handed
# back to the other group, so capacity is never wasted while either group still
# has rows left; a corpus smaller than the cap simply returns fewer rows.
TEST_RESERVE_RATIO = 0.2

_DISCLAIMER = "Candidate occurrences only; results are not compiler-accurate or exhaustive."


def _annotate_staleness(
    results: list[dict], root: Path, indexed_hashes: dict[str, str]
) -> tuple[list[dict], int]:
    """Return copies of the returned rows tagged with a freshness verdict.

    The index already stores a per-file SHA-256 to skip unchanged files during
    incremental indexing; that hash is reused here as the freshness oracle.
    Work is bounded by the distinct files present in the returned slice, never
    by repository size, and nothing is re-indexed: freshness is reported, not
    repaired.

    Rows are copied rather than mutated. The dicts in ``results`` are the same
    objects held in the in-memory index cache, so writing to them would leak a
    read-only lookup's verdict into cached state and, on the next save, onto
    disk.

    ``file_path`` values come from the persisted index, which is a local cache
    file rather than a trusted input, so the resolved path is confined to the
    repository root and oversized reads are refused. Anything unreadable,
    escaping, or oversized is reported stale rather than raising.
    """
    verdicts: dict[str, bool] = {}

    def _is_stale(rel_path: str) -> bool:
        try:
            candidate = (root / rel_path).resolve()
            if not candidate.is_relative_to(root.resolve()):
                return True
            if candidate.stat().st_size > MAX_FILE_SIZE_BYTES:
                return True
            current = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except (OSError, ValueError):
            # Missing, unreadable, or malformed backing path: stale, never an error.
            return True
        return indexed_hashes.get(rel_path) != current

    annotated: list[dict] = []
    for row in results:
        rel_path = row.get("file_path", "")
        if rel_path not in verdicts:
            verdicts[rel_path] = _is_stale(rel_path)
        annotated.append({**row, "is_stale": verdicts[rel_path]})

    return annotated, sum(1 for is_stale in verdicts.values() if is_stale)


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
    candidate_names = sorted(name for name in index.occurrences if name.lower() == query_lower)

    if not candidate_names:
        return {
            "query": query,
            "usage_filter": usage_filter,
            "total_matches": 0,
            "production_matches": 0,
            "test_matches": 0,
            "returned_production": 0,
            "returned_tests": 0,
            "stale_file_count": 0,
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
            chosen_production += production[len(chosen_production) : len(chosen_production) + spare]

        results = chosen_production + chosen_tests
    else:
        # "production_first" — production occurrences first, tests after.
        matches = production + tests
        results = matches[:limit]

    total_matches = len(matches)
    returned_tests = sum(1 for m in results if m.get("is_test_file"))
    results, stale_file_count = _annotate_staleness(results, Path(repo_path), index.files)

    tokens_saved = estimate_savings(len(results))
    return {
        "query": query,
        "usage_filter": usage_filter,
        "total_matches": total_matches,
        "production_matches": len(production),
        "test_matches": len(tests),
        "returned_production": len(results) - returned_tests,
        "returned_tests": returned_tests,
        "stale_file_count": stale_file_count,
        "results": results,
        "candidate_names": candidate_names,
        "disclaimer": _DISCLAIMER,
        "_meta": make_meta(t0, tokens_saved=tokens_saved),
    }
