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
        response = await lgrep_status(ctx=mock_ctx)
        status_data = json.loads(response)
        assert status_data["files"] == 2
        assert status_data["project"] == str(sample_project.resolve())

        # 3. Search for "login"
        # Since we use deterministic mock embeddings, we can't test semantic quality,
        # but we can test that it returns results from the database.
        response = await lgrep_search("login", ctx=mock_ctx)
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
