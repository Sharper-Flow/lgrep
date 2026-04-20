"""Tests for async query-retry path in VoyageEmbedder.

Verifies:
- embed_query_async uses asyncio.sleep (not time.sleep) for retries
- embed_query_async returns correct vectors on success
- embed_query_async raises after QUERY_MAX_RETRIES failures
- Sync embed_query path is unchanged (time.sleep still used)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lgrep.embeddings import QUERY_BASE_DELAY, QUERY_MAX_RETRIES, VoyageEmbedder


@pytest.fixture
def mock_embedder():
    """Create a VoyageEmbedder with mocked Voyage client."""
    with patch("lgrep.embeddings.voyageai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        embedder = VoyageEmbedder(api_key="test-key")
        yield embedder, mock_client


class TestEmbedQueryAsync:
    """Verify async query-retry path."""

    @pytest.mark.asyncio
    async def test_embed_query_async_success(self, mock_embedder):
        """embed_query_async returns correct vector on first attempt."""
        embedder, mock_client = mock_embedder
        mock_result = MagicMock()
        mock_result.embeddings = [[0.1] * 1024]
        mock_result.total_tokens = 50
        mock_client.embed.return_value = mock_result

        result = await embedder.embed_query_async("test query")

        assert len(result) == 1024
        assert result[0] == 0.1
        mock_client.embed.assert_called_once_with(
            texts=["test query"],
            model="voyage-code-3",
            input_type="query",
        )

    @pytest.mark.asyncio
    async def test_embed_query_async_retries_with_asyncio_sleep(self, mock_embedder):
        """embed_query_async uses asyncio.sleep for retries, not time.sleep."""
        embedder, mock_client = mock_embedder

        # Fail once, then succeed
        mock_result = MagicMock()
        mock_result.embeddings = [[0.2] * 1024]
        mock_result.total_tokens = 50

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("temporary failure")
            return mock_result

        mock_client.embed.side_effect = side_effect

        with patch("lgrep.embeddings.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await embedder.embed_query_async("retry query")

            # asyncio.sleep was called (not time.sleep)
            assert mock_sleep.call_count == 1
            # Delay should be QUERY_BASE_DELAY * 2^0 + jitter
            delay_arg = mock_sleep.call_args[0][0]
            assert delay_arg >= QUERY_BASE_DELAY  # base delay, plus jitter up to 0.5

        assert len(result) == 1024
        assert result[0] == 0.2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_embed_query_async_raises_after_max_retries(self, mock_embedder):
        """embed_query_async raises after QUERY_MAX_RETRIES failures."""
        embedder, mock_client = mock_embedder
        mock_client.embed.side_effect = RuntimeError("persistent failure")

        with patch("lgrep.embeddings.asyncio.sleep", new_callable=AsyncMock), pytest.raises(RuntimeError, match="persistent failure"):
            await embedder.embed_query_async("failing query")

        assert mock_client.embed.call_count == QUERY_MAX_RETRIES

    @pytest.mark.asyncio
    async def test_embed_query_async_tracks_tokens(self, mock_embedder):
        """embed_query_async tracks token usage like the sync path."""
        embedder, mock_client = mock_embedder
        mock_result = MagicMock()
        mock_result.embeddings = [[0.3] * 1024]
        mock_result.total_tokens = 100
        mock_client.embed.return_value = mock_result

        initial_tokens = embedder.total_tokens_used
        await embedder.embed_query_async("token tracking query")

        assert embedder.total_tokens_used == initial_tokens + 100


class TestSyncPathUnchanged:
    """Verify sync embed_query still uses time.sleep (not asyncio.sleep)."""

    def test_sync_embed_query_uses_time_sleep(self, mock_embedder):
        """Sync embed_query must still use time.sleep for backward compatibility."""
        embedder, mock_client = mock_embedder

        # Fail once, then succeed
        mock_result = MagicMock()
        mock_result.embeddings = [[0.4] * 1024]
        mock_result.total_tokens = 50

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("temporary failure")
            return mock_result

        mock_client.embed.side_effect = side_effect

        with patch("lgrep.embeddings.time.sleep") as mock_sleep:
            result = embedder.embed_query("sync retry query")

            # time.sleep was called (not asyncio.sleep)
            assert mock_sleep.call_count == 1

        assert len(result) == 1024