"""Integration tests for indexing and search."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import (
    lgrep_index,
    lgrep_search,
    lgrep_status,
    lgrep_watch_start,
    lgrep_watch_stop,
    LgrepContext,
)


@pytest.fixture
def sample_project():
    """Create a sample project with some code files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / "auth.py").write_text('''
def login(username, password):
    """Handle user login."""
    if username == "admin" and password == "secret":
        return True
    return False
''')

        (root / "db.py").write_text('''
import sqlite3

def connect():
    """Connect to database."""
    return sqlite3.connect(":memory:")
''')

        yield root


@pytest.mark.asyncio
async def test_full_flow_integration(sample_project):
    """Test full flow: index project -> check status -> search."""

    # Mock Voyage API to return deterministic vectors
    def mock_embed_docs(texts, **kwargs):
        from lgrep.embeddings import EmbeddingResult

        # Simple hash-based mock embedding for deterministic testing
        embeddings = []
        for text in texts:
            val = hash(text) % 1000 / 1000.0
            embeddings.append([val] * 1024)
        return EmbeddingResult(embeddings, len(texts) * 5, "mock")

    def mock_embed_query(query, **kwargs):
        val = hash(query) % 1000 / 1000.0
        return [val] * 1024

    mock_ctx = MagicMock(spec=Context)
    app_ctx = LgrepContext(voyage_api_key="mock-key")
    mock_ctx.request_context.lifespan_context = app_ctx

    with patch("lgrep.server.VoyageEmbedder") as mock_embedder_class:
        mock_embedder = MagicMock()
        mock_embedder.embed_documents.side_effect = mock_embed_docs
        mock_embedder.embed_query.side_effect = mock_embed_query
        mock_embedder_class.return_value = mock_embedder

        # 1. Index the project
        response = await lgrep_index(str(sample_project), ctx=mock_ctx)
        status_data = json.loads(response)
        assert status_data["file_count"] == 2
        assert status_data["chunk_count"] >= 2

        # 2. Check status
        response = await lgrep_status(path=str(sample_project), ctx=mock_ctx)
        status_data = json.loads(response)
        assert status_data["files"] == 2
        assert status_data["project"] == str(sample_project.resolve())

        # 3. Search for "login"
        # Since we use deterministic mock embeddings, we can't test semantic quality,
        # but we can test that it returns results from the database.
        response = await lgrep_search("login", path=str(sample_project), ctx=mock_ctx)
        search_data = json.loads(response)

        assert "results" in search_data
        assert len(search_data["results"]) > 0

        # Check first result
        res = search_data["results"][0]
        assert "file_path" in res
        assert "content" in res
        assert "score" in res

    # 4. Test Watcher
    response = await lgrep_watch_start(str(sample_project), ctx=mock_ctx)
    watch_data = json.loads(response)
    assert watch_data["watching"] is True
    
    # 5. Stop Watcher
    response = await lgrep_watch_stop(ctx=mock_ctx)
    stop_data = json.loads(response)
    assert stop_data["stopped"] is True


@pytest.mark.asyncio
async def test_multi_project_isolation():
    """Test that two projects are indexed independently and search results are isolated."""

    def mock_embed_docs(texts, **kwargs):
        from lgrep.embeddings import EmbeddingResult

        embeddings = []
        for text in texts:
            val = hash(text) % 1000 / 1000.0
            embeddings.append([val] * 1024)
        return EmbeddingResult(embeddings, len(texts) * 5, "mock")

    def mock_embed_query(query, **kwargs):
        val = hash(query) % 1000 / 1000.0
        return [val] * 1024

    with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
        project_a = Path(dir_a)
        project_b = Path(dir_b)

        # Project A: authentication code
        (project_a / "auth.py").write_text('''
def login(username, password):
    """Handle user login."""
    if username == "admin" and password == "secret":
        return True
    return False
''')

        # Project B: payment code (completely different domain)
        (project_b / "billing.py").write_text('''
def charge_card(card_number, amount):
    """Process credit card payment."""
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return {"status": "charged", "amount": amount}
''')

        mock_ctx = MagicMock(spec=Context)
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        mock_ctx.request_context.lifespan_context = app_ctx

        with patch("lgrep.server.VoyageEmbedder") as mock_embedder_class:
            mock_embedder = MagicMock()
            mock_embedder.embed_documents.side_effect = mock_embed_docs
            mock_embedder.embed_query.side_effect = mock_embed_query
            mock_embedder_class.return_value = mock_embedder

            # 1. Index project A
            resp_a = json.loads(await lgrep_index(str(project_a), ctx=mock_ctx))
            assert resp_a["file_count"] == 1

            # 2. Index project B
            resp_b = json.loads(await lgrep_index(str(project_b), ctx=mock_ctx))
            assert resp_b["file_count"] == 1

            # 3. Both projects are in the context dict
            assert len(app_ctx.projects) == 2

            # 4. Status without path returns both projects
            resp_all = json.loads(await lgrep_status(ctx=mock_ctx))
            assert len(resp_all["projects"]) == 2
            project_paths = {p["project"] for p in resp_all["projects"]}
            assert str(project_a.resolve()) in project_paths
            assert str(project_b.resolve()) in project_paths

            # 5. Status with path returns only that project
            resp_one = json.loads(await lgrep_status(path=str(project_a), ctx=mock_ctx))
            assert resp_one["project"] == str(project_a.resolve())
            assert resp_one["files"] == 1

            # 6. Search project A — results should only contain files from A
            resp_search_a = json.loads(
                await lgrep_search("login", path=str(project_a), ctx=mock_ctx)
            )
            assert "results" in resp_search_a
            for result in resp_search_a["results"]:
                assert "billing.py" not in result["file_path"]

            # 7. Search project B — results should only contain files from B
            resp_search_b = json.loads(
                await lgrep_search("payment", path=str(project_b), ctx=mock_ctx)
            )
            assert "results" in resp_search_b
            for result in resp_search_b["results"]:
                assert "auth.py" not in result["file_path"]

            # 8. Search unindexed project returns error
            resp_err = json.loads(
                await lgrep_search("test", path="/not/indexed", ctx=mock_ctx)
            )
            assert "error" in resp_err
            assert "not indexed" in resp_err["error"].lower()
