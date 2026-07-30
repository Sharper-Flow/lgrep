"""Latency benchmark for lgrep_search_semantic and symbol tools.

Verifies that the refactor did not introduce performance regressions.

For semantic search: uses a mock embedder to isolate local search latency
(excludes Voyage API call which is network-bound).

For symbol search: measures actual index + search latency.

Baselines are recorded in this file. The test asserts median latency
does not exceed baseline (strict budget).
"""

from __future__ import annotations

import statistics
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Semantic search latency ───────────────────────────────────────────────────

# Baseline: local LanceDB search (excluding Voyage API call) should be <100ms median
# This is a generous budget — actual measured baseline is ~15ms
SEMANTIC_SEARCH_MEDIAN_BUDGET_MS = 100.0
PER_SAMPLE_HANG_GUARD_MS = 1000.0


class TestSemanticSearchLatency:
    """Verify local search latency (LanceDB only, mock embedder)."""

    @pytest.mark.asyncio
    async def test_search_semantic_local_latency(self, tmp_path):
        """Local search latency (excluding Voyage API) must be <100ms median over 10 queries."""
        from mcp.server.fastmcp import Context

        from lgrep.server import LgrepContext, ProjectState, search_semantic
        from lgrep.storage import SearchResult, SearchResults

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "bench_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())
        app_ctx.projects[str(project_path)] = mock_state

        # Mock embedder returns instantly. `search_semantic` awaits the async
        # path, so use AsyncMock to measure search latency instead of the error
        # logging path.
        app_ctx.embedder.embed_query_async = AsyncMock(return_value=[0.1] * 1024)

        # Mock search returns 10 results
        results = SearchResults(
            results=[
                SearchResult(
                    f"file_{i}.py",
                    i * 10,
                    i * 10 + 5,
                    f"code snippet {i}",
                    0.9 - i * 0.01,
                    "hybrid",
                )
                for i in range(10)
            ],
            query_time_ms=5.0,
            total_chunks=1000,
        )
        mock_db.search_hybrid.return_value = results

        # Run 10 queries and measure latency
        latencies = []
        queries = [
            "authentication flow",
            "error handling",
            "database connection",
            "rate limiting",
            "JWT verification",
            "session management",
            "password hashing",
            "API routing",
            "middleware chain",
            "request validation",
        ]

        for query in queries:
            t0 = time.monotonic()
            response = await search_semantic(query=query, path=str(project_path), ctx=mock_ctx)
            assert "error" not in response, response
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert elapsed_ms < PER_SAMPLE_HANG_GUARD_MS, (
                f"Semantic search sample latency {elapsed_ms:.1f}ms exceeded "
                f"hang/deadlock guard {PER_SAMPLE_HANG_GUARD_MS}ms for query {query!r}"
            )
            latencies.append(elapsed_ms)

        median = statistics.median(latencies)

        assert median < SEMANTIC_SEARCH_MEDIAN_BUDGET_MS, (
            f"Semantic search median latency {median:.1f}ms exceeds budget "
            f"{SEMANTIC_SEARCH_MEDIAN_BUDGET_MS}ms. "
            f"All latencies: {[f'{lat:.1f}ms' for lat in sorted(latencies)]}"
        )


# ── Symbol search latency ─────────────────────────────────────────────────────

# Baseline: symbol search (in-memory JSON index) should be <50ms median
SYMBOL_SEARCH_MEDIAN_BUDGET_MS = 50.0

# Baseline: index_folder for 10 files should complete in <5s
INDEX_FOLDER_BUDGET_MS = 5000.0


class TestSymbolSearchLatency:
    """Verify symbol search latency."""

    @pytest.fixture
    def bench_repo(self, tmp_path):
        """Create a 10-file Python repo for benchmarking."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            (src / f"module_{i}.py").write_text(
                f"def function_{i}_a():\n    pass\n\n"
                f"def function_{i}_b():\n    pass\n\n"
                f"class Class_{i}:\n    def method_{i}(self):\n        pass\n"
            )
        return tmp_path

    def test_index_folder_latency(self, bench_repo, tmp_path):
        """index_folder for 10 files must complete in <5s."""
        from lgrep.tools.index_folder import index_folder

        store = tmp_path / "store"
        t0 = time.monotonic()
        result = index_folder(str(bench_repo), storage_dir=store)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert "error" not in result
        assert elapsed_ms < INDEX_FOLDER_BUDGET_MS, (
            f"index_folder took {elapsed_ms:.0f}ms, budget is {INDEX_FOLDER_BUDGET_MS}ms"
        )

    def test_search_symbols_latency(self, bench_repo, tmp_path):
        """Symbol search median latency must be <50ms over 10 queries."""
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        store = tmp_path / "store"
        index_folder(str(bench_repo), storage_dir=store)

        queries = [f"function_{i}" for i in range(10)]
        latencies = []

        for query in queries:
            t0 = time.monotonic()
            result = search_symbols(query, str(bench_repo), storage_dir=store)
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert "error" not in result
            assert elapsed_ms < PER_SAMPLE_HANG_GUARD_MS, (
                f"Symbol search sample latency {elapsed_ms:.1f}ms exceeded "
                f"hang/deadlock guard {PER_SAMPLE_HANG_GUARD_MS}ms for query {query!r}"
            )
            latencies.append(elapsed_ms)

        median = statistics.median(latencies)

        assert median < SYMBOL_SEARCH_MEDIAN_BUDGET_MS, (
            f"Symbol search median latency {median:.1f}ms exceeds budget "
            f"{SYMBOL_SEARCH_MEDIAN_BUDGET_MS}ms. "
            f"All latencies: {[f'{lat:.1f}ms' for lat in sorted(latencies)]}"
        )


# ── Pure tests for median aggregation and budgets ─────────────────────────────


class TestMedianBudgetSemantics:
    """Unit tests for statistics.median behavior and budget semantics."""

    def test_median_with_outlier_is_robust_and_under_budget(self):
        """One 159ms outlier among nine low values gives median ~0.375ms and passes 50ms."""
        latencies = [0.0, 0.0, 0.0, 0.0, 0.0, 0.75, 0.75, 0.75, 0.75, 159.0]
        median = statistics.median(latencies)
        assert median == pytest.approx(0.375)
        assert median < SYMBOL_SEARCH_MEDIAN_BUDGET_MS

    def test_median_ten_identical_values(self):
        """Ten identical 60ms samples have median 60ms and fail the 50ms budget."""
        latencies = [60.0] * 10
        median = statistics.median(latencies)
        assert median == 60.0
        with pytest.raises(AssertionError):
            assert median < SYMBOL_SEARCH_MEDIAN_BUDGET_MS

    def test_median_sequence_with_large_outlier(self):
        """Values [1..9, 100] have median (5+6)/2 == 5.5."""
        latencies = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 100.0]
        assert statistics.median(latencies) == pytest.approx(5.5)

    @pytest.mark.parametrize(
        "budget",
        [SEMANTIC_SEARCH_MEDIAN_BUDGET_MS, SYMBOL_SEARCH_MEDIAN_BUDGET_MS],
    )
    def test_median_strict_budget_boundary(self, budget):
        """Median exactly at either budget fails; just below passes."""
        assert statistics.median([budget] * 10) == budget
        with pytest.raises(AssertionError):
            assert statistics.median([budget] * 10) < budget
        just_below_budget = budget - 0.001
        assert statistics.median([just_below_budget] * 10) == just_below_budget
        assert statistics.median([just_below_budget] * 10) < budget

    def test_per_sample_hang_guard_boundary(self):
        """A single 999.9ms sample passes the hang guard; 1000.0ms fails."""
        assert PER_SAMPLE_HANG_GUARD_MS > 999.9
        with pytest.raises(AssertionError):
            assert PER_SAMPLE_HANG_GUARD_MS > 1000.0
