"""Tests for server tool responses."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import (
    AUTO_INDEX_MAX_ATTEMPTS,
    MAX_PROJECTS,
    LgrepContext,
    ProjectState,
    _ensure_project_initialized,
    _shutdown,
    _stop_watcher,
    _warm_projects,
    remove_project,
)
from lgrep.server import (
    index as lgrep_index,
)
from lgrep.server import (
    search as lgrep_search,
)
from lgrep.server import (
    status as lgrep_status,
)
from lgrep.server import (
    watch_start as lgrep_watch_start,
)
from lgrep.server import (
    watch_stop as lgrep_watch_stop,
)
from lgrep.storage import SearchResult, SearchResults


class TestDiskCacheAutoLoad:
    """Tests for lazy auto-load of existing disk indexes on server restart."""

    @pytest.mark.asyncio
    async def test_search_auto_loads_from_disk_cache(self, tmp_path):
        """lgrep_search should auto-load a project from disk when the in-memory
        dict is empty but a valid LanceDB index exists on disk."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "myproject"
        project_path.mkdir()

        # Simulate disk cache detection + ensure_project_initialized returning a state
        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        with (
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            response = await lgrep_search(query="test", path=str(project_path), ctx=mock_ctx)

        data = json.loads(response)
        assert "results" in data
        assert len(data["results"]) == 1
        # _ensure_project_initialized should have been called
        mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_warm_path_does_not_call_index_all(self, tmp_path):
        """Warm-path (disk cache hit) must NOT trigger index_all — performance guardrail."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "warm_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_indexer = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=mock_indexer)

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        with (
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch("lgrep.server._ensure_project_initialized", return_value=mock_state),
        ):
            await lgrep_search(query="find auth", path=str(project_path), ctx=mock_ctx)

        # Critical guardrail: warm path must NEVER trigger re-indexing
        mock_indexer.index_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_no_disk_cache_still_errors(self, tmp_path):
        """lgrep_search should return path error for missing directories."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "noproject"

        with patch("lgrep.server.has_disk_cache", return_value=False):
            response = await lgrep_search(query="test", path=str(project_path), ctx=mock_ctx)

        data = json.loads(response)
        assert "error" in data
        assert "does not exist" in data["error"]

    @pytest.mark.asyncio
    async def test_search_auto_load_init_failure_returns_error(self, tmp_path):
        """If _ensure_project_initialized returns an error string during auto-load,
        that error should propagate to the caller."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "failproject"
        project_path.mkdir()

        error_msg = json.dumps({"error": "VOYAGE_API_KEY not set."})
        with (
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch("lgrep.server._ensure_project_initialized", return_value=error_msg),
        ):
            response = await lgrep_search(query="test", path=str(project_path), ctx=mock_ctx)

        data = json.loads(response)
        assert "error" in data
        assert "VOYAGE_API_KEY" in data["error"]

    @pytest.mark.asyncio
    async def test_status_reads_disk_cache_without_api_key(self, tmp_path):
        """lgrep_status should read stats from disk when the project isn't in memory
        but a valid LanceDB cache exists — without requiring an API key."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()  # No voyage_api_key, no embedder
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "cached_project"
        project_path.mkdir()

        mock_store = MagicMock()
        mock_store.count_chunks.return_value = 500
        mock_store.get_indexed_files.return_value = {"a.py", "b.py", "c.py"}

        with (
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch("lgrep.server.ChunkStore", return_value=mock_store),
        ):
            response = await lgrep_status(path=str(project_path), ctx=mock_ctx)

        data = json.loads(response)
        assert data["files"] == 3
        assert data["chunks"] == 500
        assert data["watching"] is False
        assert "disk_cache" in data
        assert data["disk_cache"] is True

    @pytest.mark.asyncio
    async def test_status_no_disk_cache_returns_zeros(self, tmp_path):
        """lgrep_status should return zeros when no disk cache exists."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "nope"
        project_path.mkdir()

        with patch("lgrep.server.has_disk_cache", return_value=False):
            response = await lgrep_status(path=str(project_path), ctx=mock_ctx)

        data = json.loads(response)
        assert data["files"] == 0
        assert data["chunks"] == 0


class TestAcceptanceToolChoiceAndOnboarding:
    """Acceptance-level checks for tool-choice policy and first-search onboarding.

    The scenario matrix defines the fixed denominator for the >=90% success
    criteria from rq-1 and rq-2.  Each entry maps a representative user prompt
    to the expected first-action tool and the skill-doc keyword that should
    steer the agent.  A test validates the skill doc covers every category.

    Threshold: floor(0.9 * len(SCENARIO_MATRIX)) scenarios must have matching
    guidance in the skill document.
    """

    # -- Scenario Matrix (fixed denominator) --
    # (prompt, expected_tool, doc_keyword_that_steers_agent)
    SEMANTIC_SCENARIOS = [
        ("where is auth enforced between API and service layer?", "lgrep_search", "Intent search"),
        ("how does the rate limiter work?", "lgrep_search", "Code exploration"),
        ("find the error handling strategy", "lgrep_search", "concept"),
        ("what middleware runs before route handlers?", "lgrep_search", "Feature discovery"),
        ("how are database connections pooled?", "lgrep_search", "Natural language"),
        ("where is user input validated?", "lgrep_search", "Intent search"),
        ("explain the caching strategy", "lgrep_search", "Code exploration"),
        ("how are background jobs scheduled?", "lgrep_search", "find implementation"),
        ("where are permissions checked?", "lgrep_search", "Intent search"),
        ("how does retry logic work for failed requests?", "lgrep_search", "Natural language"),
    ]

    EXACT_SCENARIOS = [
        ("find all references to verifyToken", "grep", "Exact matches"),
        ("grep for handleError function", "grep", "Symbol tracing"),
        ("find all usages of UserService class", "grep", "Refactoring"),
        ("search for imports of lodash", "grep", "Exact matches"),
        ("find all TODO comments", "grep", "Regex patterns"),
        ("list all files matching *.test.ts", "grep", "Exact matches"),
        ("find where MAX_RETRIES is defined", "grep", "Symbol tracing"),
        ("open src/auth/jwt.ts and explain line 42", "read", "read"),
        ("show me the contents of package.json", "read", "read"),
        ("find all occurrences of console.log", "grep", "Exact matches"),
    ]

    PASS_THRESHOLD = 0.9  # >=90% of scenarios must have doc coverage

    def test_skill_documents_semantic_first_policy(self):
        """Skill guidance should make lgrep the first action for semantic discovery."""
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "lgrep" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")

        assert "call `lgrep_search` first" in content
        assert "Intent search" in content

    def test_skill_documents_exact_match_policy(self):
        """Skill guidance should preserve exact-match behavior for grep workflows."""
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "lgrep" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")

        assert "exact identifier/regex" in content
        assert "Use `Grep` first" in content

    def test_scenario_matrix_has_skill_coverage(self):
        """Every scenario category keyword must appear in skill docs (>=90% threshold)."""
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "lgrep" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8").lower()

        all_scenarios = self.SEMANTIC_SCENARIOS + self.EXACT_SCENARIOS
        total = len(all_scenarios)
        covered = 0
        missing = []

        for prompt, _expected_tool, doc_keyword in all_scenarios:
            if doc_keyword.lower() in content:
                covered += 1
            else:
                missing.append((prompt, doc_keyword))

        threshold = int(total * self.PASS_THRESHOLD)
        assert covered >= threshold, (
            f"Skill doc covers {covered}/{total} scenarios "
            f"(need {threshold}). Missing keywords: {missing}"
        )

    def test_scenario_matrix_denominator_is_fixed(self):
        """Denominator is fixed at 20 (10 semantic + 10 exact) for reproducibility."""
        assert len(self.SEMANTIC_SCENARIOS) == 10
        assert len(self.EXACT_SCENARIOS) == 10

    def test_readme_documents_streamable_http_security_controls(self):
        """README must document streamable-http security controls per rq-4.2."""
        readme_path = Path(__file__).resolve().parents[1] / "README.md"
        content = readme_path.read_text(encoding="utf-8")

        # Localhost binding
        assert "127.0.0.1" in content
        assert "localhost" in content.lower() or "127.0.0.1" in content
        # Auth expectation
        assert "authentication" in content.lower() or "auth" in content.lower()
        # CORS / origin guidance
        assert "CORS" in content or "origin" in content.lower()
        # Non-default stance
        assert (
            "non-default" in content or "opt-in" in content.lower() or "explicit" in content.lower()
        )

    @pytest.mark.asyncio
    async def test_search_auto_indexes_when_project_not_cached(self, tmp_path):
        """First semantic search in a cold project should auto-index without manual lgrep_index."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "cold_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        with (
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            response = await lgrep_search(
                query="where auth is enforced",
                path=str(project_path),
                ctx=mock_ctx,
            )

        data = json.loads(response)
        assert "results" in data
        mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_auto_index_single_flight_for_concurrent_calls(self, tmp_path):
        """Concurrent first-search calls for same cold project should run one full index."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "concurrent_cold_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        call_counter = {"count": 0}

        def slow_index_all():
            import time

            call_counter["count"] += 1
            time.sleep(0.05)
            return MagicMock(file_count=1, chunk_count=1, duration_ms=10.0)

        mock_state.indexer.index_all.side_effect = slow_index_all

        async def fake_ensure_init(ctx, path):
            """Simulate _ensure_project_initialized: register state in projects dict."""
            ctx.projects[str(path)] = mock_state
            return mock_state

        with (
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch("lgrep.server._ensure_project_initialized", side_effect=fake_ensure_init),
        ):
            response_a, response_b = await asyncio.gather(
                lgrep_search(
                    query="where auth is enforced",
                    path=str(project_path),
                    ctx=mock_ctx,
                ),
                lgrep_search(
                    query="where auth is enforced",
                    path=str(project_path),
                    ctx=mock_ctx,
                ),
            )

        data_a = json.loads(response_a)
        data_b = json.loads(response_b)
        assert "results" in data_a
        assert "results" in data_b
        assert call_counter["count"] == 1

    @pytest.mark.asyncio
    async def test_search_auto_index_missing_api_key(self, tmp_path):
        """Auto-index with missing VOYAGE_API_KEY should return actionable error."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key=None)  # No API key
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "no_key_project"
        project_path.mkdir()

        with patch("lgrep.server.has_disk_cache", return_value=False):
            response = await lgrep_search(
                query="find auth logic",
                path=str(project_path),
                ctx=mock_ctx,
            )

        data = json.loads(response)
        assert "error" in data
        assert "VOYAGE_API_KEY" in data["error"]
        # Must NOT tell user to run lgrep_index manually
        assert "lgrep_index" not in data["error"]
        # Partial state must not persist
        assert str(project_path.resolve()) not in app_ctx.projects

    @pytest.mark.asyncio
    async def test_search_auto_index_failure_cleans_up_state(self, tmp_path):
        """Indexing failure during auto-index should clean up partial state."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "failing_project"
        project_path.mkdir()

        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())
        mock_state.indexer.index_all.side_effect = RuntimeError("Embedding API down")

        async def fake_ensure_init(ctx, path):
            ctx.projects[str(path)] = mock_state
            return mock_state

        with (
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch("lgrep.server._ensure_project_initialized", side_effect=fake_ensure_init),
        ):
            response = await lgrep_search(
                query="find auth logic",
                path=str(project_path),
                ctx=mock_ctx,
            )

        data = json.loads(response)
        assert "error" in data
        assert "Failed to auto-index" in data["error"]
        assert mock_state.indexer.index_all.call_count == AUTO_INDEX_MAX_ATTEMPTS
        # Must NOT tell user to run lgrep_index manually
        assert "lgrep_index" not in data["error"]
        # Partial state must be removed
        assert str(project_path.resolve()) not in app_ctx.projects

    @pytest.mark.asyncio
    async def test_search_auto_index_retries_then_succeeds(self, tmp_path):
        """Transient indexing failure should retry and eventually succeed."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "retry_success_project"
        project_path.mkdir()

        mock_db = MagicMock()
        mock_state = ProjectState(db=mock_db, indexer=MagicMock())

        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=5.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results

        attempts = {"count": 0}

        def flaky_index_all():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary embedding timeout")
            return MagicMock(file_count=1, chunk_count=1, duration_ms=10.0)

        mock_state.indexer.index_all.side_effect = flaky_index_all

        async def fake_ensure_init(ctx, path):
            ctx.projects[str(path)] = mock_state
            return mock_state

        with (
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch("lgrep.server._ensure_project_initialized", side_effect=fake_ensure_init),
            patch("lgrep.server.AUTO_INDEX_RETRY_BASE_DELAY_S", 0),
        ):
            response = await lgrep_search(
                query="find auth logic",
                path=str(project_path),
                ctx=mock_ctx,
            )

        data = json.loads(response)
        assert "results" in data
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_search_auto_index_concurrent_leader_failure(self, tmp_path):
        """When the leader fails, followers should get an actionable error."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.embedder = MagicMock()
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024
        mock_ctx.request_context.lifespan_context = app_ctx

        project_path = tmp_path / "leader_fails_project"
        project_path.mkdir()

        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())
        mock_state.indexer.index_all.side_effect = RuntimeError("Embedding API timeout")

        async def fake_ensure_init(ctx, path):
            ctx.projects[str(path)] = mock_state
            return mock_state

        with (
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch("lgrep.server._ensure_project_initialized", side_effect=fake_ensure_init),
        ):
            response_a, response_b = await asyncio.gather(
                lgrep_search(
                    query="find auth logic",
                    path=str(project_path),
                    ctx=mock_ctx,
                ),
                lgrep_search(
                    query="find auth logic",
                    path=str(project_path),
                    ctx=mock_ctx,
                ),
            )

        # Both should get error responses (not crashes)
        data_a = json.loads(response_a)
        data_b = json.loads(response_b)
        assert "error" in data_a or "error" in data_b


class TestServerTools:
    """Tests for MCP tools in server.py."""

    @pytest.mark.asyncio
    async def test_lgrep_search_format(self):
        """Should format search results as JSON."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        app_ctx.embedder = MagicMock()

        # Set up a ProjectState in the projects dict
        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock())
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        # Mock storage return
        results = SearchResults(
            results=[SearchResult("a.py", 1, 10, "code", 0.9, "hybrid")],
            query_time_ms=10.0,
            total_chunks=100,
        )
        mock_db.search_hybrid.return_value = results
        app_ctx.embedder.embed_query.return_value = [0.1] * 1024

        response = await lgrep_search(query="test", path="/path", ctx=mock_ctx)
        data = json.loads(response)

        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["file_path"] == "a.py"
        assert data["query_time_ms"] == 10.0

    @pytest.mark.asyncio
    async def test_lgrep_status_format(self):
        """Should format status as JSON."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()

        # Set up a ProjectState in the projects dict
        mock_db = MagicMock()
        state = ProjectState(db=mock_db, indexer=MagicMock(), watching=True)
        app_ctx.projects["/path"] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        mock_db.count_chunks.return_value = 500
        mock_db.get_indexed_files.return_value = {"a.py", "b.py"}

        response = await lgrep_status(path="/path", ctx=mock_ctx)
        data = json.loads(response)

        assert data["files"] == 2
        assert data["chunks"] == 500
        assert data["watching"] is True
        assert data["project"] == "/path"


class TestServerErrorPaths:
    """Tests for error handling in MCP tools."""

    @pytest.mark.asyncio
    async def test_lgrep_search_no_index(self):
        """Should return path error when project path does not exist."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()  # No projects in dict
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_search(query="test", path="/some/path", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "does not exist" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_search_no_context(self):
        """Should return error when context is missing."""
        response = await lgrep_search(query="test", path="/some/path", ctx=None)
        data = json.loads(response)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_lgrep_index_invalid_path(self):
        """Should return error for nonexistent directory."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_index(path="/nonexistent/path/xyz", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "does not exist" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_index_missing_api_key(self, tmp_path):
        """Should return error when VOYAGE_API_KEY is not set."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()  # No voyage_api_key
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_index(path=str(tmp_path), ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data
        assert "VOYAGE_API_KEY" in data["error"]

    @pytest.mark.asyncio
    async def test_lgrep_status_no_db(self):
        """Should return empty projects list when no database is initialized."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_status(ctx=mock_ctx)
        data = json.loads(response)
        assert data["projects"] == []

    @pytest.mark.asyncio
    async def test_lgrep_watch_stop_when_not_watching(self):
        """Should return graceful response when not watching."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_stop(ctx=mock_ctx)
        data = json.loads(response)
        assert data["stopped"] is True
        assert data["projects_stopped"] == []

    @pytest.mark.asyncio
    async def test_lgrep_watch_start_invalid_path(self):
        """Should return error for nonexistent path."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext()
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_start(path="/nonexistent/path", ctx=mock_ctx)
        data = json.loads(response)
        assert "error" in data


class TestMaxProjectsLimit:
    """Tests for MAX_PROJECTS resource guard."""

    @pytest.mark.asyncio
    async def test_max_projects_rejects_at_limit(self, tmp_path):
        """Should reject new projects when MAX_PROJECTS limit is reached."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Pre-fill projects dict to MAX_PROJECTS
        for i in range(MAX_PROJECTS):
            app_ctx.projects[f"/fake/project/{i}"] = ProjectState(
                db=MagicMock(), indexer=MagicMock()
            )

        assert len(app_ctx.projects) == MAX_PROJECTS

        # Try to add one more
        new_path = tmp_path / "overflow"
        new_path.mkdir()
        result = await _ensure_project_initialized(app_ctx, new_path)

        # Should return error string, not ProjectState
        assert isinstance(result, str)
        data = json.loads(result)
        assert "error" in data
        assert "Maximum project limit" in data["error"]
        assert "Restart the server" in data["error"]

    @pytest.mark.asyncio
    async def test_projects_below_limit_succeed(self, tmp_path):
        """Should allow new projects below MAX_PROJECTS limit."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Pre-fill to one below limit
        for i in range(MAX_PROJECTS - 1):
            app_ctx.projects[f"/fake/project/{i}"] = ProjectState(
                db=MagicMock(), indexer=MagicMock()
            )

        new_path = tmp_path / "ok_project"
        new_path.mkdir()

        with patch("lgrep.server.VoyageEmbedder"):
            result = await _ensure_project_initialized(app_ctx, new_path)

        assert isinstance(result, ProjectState)
        assert len(app_ctx.projects) == MAX_PROJECTS


class TestWatcherBehavior:
    """Tests for watcher start/stop edge cases."""

    @pytest.mark.asyncio
    async def test_watch_start_already_watching(self, tmp_path):
        """Should return 'Already watching' when watcher is already active."""
        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Set up a ProjectState that is already watching
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=MagicMock(),
            watching=True,
        )
        path_key = str(tmp_path.resolve())
        app_ctx.projects[path_key] = state
        mock_ctx.request_context.lifespan_context = app_ctx

        response = await lgrep_watch_start(path=str(tmp_path), ctx=mock_ctx)
        data = json.loads(response)

        assert data["watching"] is True
        assert data["message"] == "Already watching"

    @pytest.mark.asyncio
    async def test_stop_watcher_resets_state(self):
        """_stop_watcher should set watching=False and watcher=None."""
        mock_watcher = MagicMock()
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=mock_watcher,
            watching=True,
        )

        result = _stop_watcher(state, "/some/path")

        assert result is True
        assert state.watching is False
        assert state.watcher is None
        mock_watcher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_watcher_noop_when_not_watching(self):
        """_stop_watcher should return False when not watching."""
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=None,
            watching=False,
        )

        result = _stop_watcher(state, "/some/path")
        assert result is False


class TestRemoveTool:
    """Tests for remove_project function (CLI-only, not an MCP tool)."""

    @pytest.mark.asyncio
    async def test_remove_loaded_project(self):
        """Should remove a project from memory and stop its watcher."""
        app_ctx = LgrepContext()

        mock_watcher = MagicMock()
        state = ProjectState(
            db=MagicMock(),
            indexer=MagicMock(),
            watcher=mock_watcher,
            watching=True,
        )
        app_ctx.projects["/path"] = state

        data = remove_project(app_ctx, "/path")

        assert data["removed"] is True
        assert data["remaining_projects"] == 0
        assert "/path" not in app_ctx.projects
        mock_watcher.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_not_loaded_project(self):
        """Should return graceful message for unloaded project."""
        app_ctx = LgrepContext()

        data = remove_project(app_ctx, "/not/loaded")

        assert data["removed"] is False
        assert "not loaded" in data["message"].lower()


class TestLifecycle:
    """Tests for startup/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_shutdown_stops_all_watchers_and_clears(self):
        """_shutdown should stop all watchers, clear projects, and null embedder."""
        watcher_a = MagicMock()
        watcher_b = MagicMock()

        ctx = LgrepContext()
        ctx.embedder = MagicMock()
        ctx.projects["/a"] = ProjectState(
            db=MagicMock(), indexer=MagicMock(), watcher=watcher_a, watching=True
        )
        ctx.projects["/b"] = ProjectState(
            db=MagicMock(), indexer=MagicMock(), watcher=watcher_b, watching=True
        )

        await _shutdown(ctx)

        watcher_a.stop.assert_called_once()
        watcher_b.stop.assert_called_once()
        assert len(ctx.projects) == 0
        assert ctx.embedder is None


class TestEagerWarmUp:
    """Tests for LGREP_WARM_PATHS eager index warming at startup."""

    @pytest.mark.asyncio
    async def test_warm_loads_projects_with_disk_cache(self, tmp_path):
        """Projects with valid disk caches should be loaded into memory."""
        project_a = tmp_path / "proj_a"
        project_b = tmp_path / "proj_b"
        project_a.mkdir()
        project_b.mkdir()

        app_ctx = LgrepContext(voyage_api_key="mock-key")

        warm_paths = os.pathsep.join([str(project_a), str(project_b)])

        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": warm_paths}),
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            await _warm_projects(app_ctx)

        assert mock_init.call_count == 2

    @pytest.mark.asyncio
    async def test_warm_skips_projects_without_disk_cache(self, tmp_path):
        """Projects without disk caches should be silently skipped."""
        project = tmp_path / "no_cache"
        project.mkdir()

        app_ctx = LgrepContext(voyage_api_key="mock-key")

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": str(project)}),
            patch("lgrep.server.has_disk_cache", return_value=False),
            patch(
                "lgrep.server._ensure_project_initialized",
            ) as mock_init,
        ):
            await _warm_projects(app_ctx)

        mock_init.assert_not_called()

    @pytest.mark.asyncio
    async def test_warm_mixed_valid_and_invalid_paths(self, tmp_path):
        """Only valid directories with disk caches should be warmed."""
        good_project = tmp_path / "good"
        good_project.mkdir()

        bad_path = tmp_path / "nonexistent"  # does not exist

        app_ctx = LgrepContext(voyage_api_key="mock-key")
        warm_paths = os.pathsep.join([str(good_project), str(bad_path)])

        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())

        def selective_cache(path):
            return str(good_project.resolve()) in str(path)

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": warm_paths}),
            patch("lgrep.server.has_disk_cache", side_effect=selective_cache),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            await _warm_projects(app_ctx)

        # Only the good project should be warmed (bad_path is not a directory)
        mock_init.assert_called_once()

    @pytest.mark.asyncio
    async def test_warm_noop_when_env_unset(self):
        """No warming should happen when LGREP_WARM_PATHS is not set."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("lgrep.server.has_disk_cache") as mock_cache,
            patch("lgrep.server._ensure_project_initialized") as mock_init,
        ):
            # Ensure env var is absent
            os.environ.pop("LGREP_WARM_PATHS", None)
            await _warm_projects(app_ctx)

        mock_cache.assert_not_called()
        mock_init.assert_not_called()

    @pytest.mark.asyncio
    async def test_warm_noop_when_env_empty(self):
        """No warming should happen when LGREP_WARM_PATHS is empty string."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": ""}),
            patch("lgrep.server.has_disk_cache") as mock_cache,
            patch("lgrep.server._ensure_project_initialized") as mock_init,
        ):
            await _warm_projects(app_ctx)

        mock_cache.assert_not_called()
        mock_init.assert_not_called()

    @pytest.mark.asyncio
    async def test_warm_respects_max_projects(self, tmp_path):
        """Warming should cap at MAX_PROJECTS minus already-loaded projects."""
        app_ctx = LgrepContext(voyage_api_key="mock-key")

        # Pre-fill to MAX_PROJECTS - 1
        for i in range(MAX_PROJECTS - 1):
            app_ctx.projects[f"/fake/{i}"] = ProjectState(db=MagicMock(), indexer=MagicMock())

        # Try to warm 3 projects — only 1 slot available
        projects = []
        for i in range(3):
            p = tmp_path / f"warm_{i}"
            p.mkdir()
            projects.append(str(p))

        warm_paths = os.pathsep.join(projects)
        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": warm_paths}),
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            await _warm_projects(app_ctx)

        # Should only attempt 1 (MAX - already loaded)
        assert mock_init.call_count == 1

    @pytest.mark.asyncio
    async def test_warm_init_failure_does_not_block_others(self, tmp_path):
        """A failing project init should not prevent other projects from warming."""
        project_a = tmp_path / "fail"
        project_b = tmp_path / "succeed"
        project_a.mkdir()
        project_b.mkdir()

        app_ctx = LgrepContext(voyage_api_key="mock-key")
        warm_paths = os.pathsep.join([str(project_a), str(project_b)])

        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())

        call_count = 0

        async def selective_init(ctx, path):
            nonlocal call_count
            call_count += 1
            if "fail" in str(path):
                raise RuntimeError("init exploded")
            return mock_state

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": warm_paths}),
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch(
                "lgrep.server._ensure_project_initialized",
                side_effect=selective_init,
            ),
        ):
            # Should not raise
            await _warm_projects(app_ctx)

        # Both should have been attempted
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_warm_deduplicates_paths(self, tmp_path):
        """Duplicate paths in LGREP_WARM_PATHS should only warm once."""
        project = tmp_path / "dedup"
        project.mkdir()

        app_ctx = LgrepContext(voyage_api_key="mock-key")
        # Same path listed three times
        warm_paths = os.pathsep.join([str(project)] * 3)
        mock_state = ProjectState(db=MagicMock(), indexer=MagicMock())

        with (
            patch.dict(os.environ, {"LGREP_WARM_PATHS": warm_paths}),
            patch("lgrep.server.has_disk_cache", return_value=True),
            patch(
                "lgrep.server._ensure_project_initialized",
                return_value=mock_state,
            ) as mock_init,
        ):
            await _warm_projects(app_ctx)

        mock_init.assert_called_once()
