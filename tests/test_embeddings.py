"""Tests for Voyage AI embedding client."""

from unittest.mock import MagicMock, patch

import pytest

from lgrep.embeddings import EmbeddingResult, VoyageEmbedder


class TestVoyageEmbedder:
    """Tests for VoyageEmbedder class."""

    def test_init_requires_api_key(self) -> None:
        """Should raise error if no API key provided."""
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="Voyage API key required"),
        ):
            VoyageEmbedder()

    def test_init_with_api_key_param(self) -> None:
        """Should accept API key as parameter."""
        with patch("voyageai.Client") as mock_client:
            embedder = VoyageEmbedder(api_key="test-key")
            assert embedder.api_key == "test-key"
            mock_client.assert_called_once_with(api_key="test-key")

    def test_init_with_env_var(self) -> None:
        """Should read API key from environment."""
        with (
            patch.dict("os.environ", {"VOYAGE_API_KEY": "env-key"}),
            patch("voyageai.Client") as mock_client,
        ):
            embedder = VoyageEmbedder()
            assert embedder.api_key == "env-key"
            mock_client.assert_called_once_with(api_key="env-key")

    def test_embed_documents_empty(self) -> None:
        """Should handle empty document list."""
        with patch("voyageai.Client"):
            embedder = VoyageEmbedder(api_key="test-key")
            result = embedder.embed_documents([])

            assert result.embeddings == []
            assert result.token_usage == 0
            assert result.model == "voyage-code-3"

    def test_embed_documents_single_batch(self) -> None:
        """Should embed documents in a single batch."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024, [0.2] * 1024]
        mock_response.total_tokens = 100

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            result = embedder.embed_documents(["doc1", "doc2"])

            assert len(result.embeddings) == 2
            assert result.token_usage == 100
            mock_client.embed.assert_called_once_with(
                texts=["doc1", "doc2"],
                model="voyage-code-3",
                input_type="document",
            )

    def test_embed_documents_batching(self) -> None:
        """Should batch large document lists."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024]
        mock_response.total_tokens = 50

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            # 150 documents with batch_size=50 should make 3 calls
            docs = [f"doc{i}" for i in range(150)]
            result = embedder.embed_documents(docs, batch_size=50)

            assert mock_client.embed.call_count == 3
            assert result.token_usage == 150  # 3 batches * 50 tokens

    def test_embed_query(self) -> None:
        """Should embed a single query."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.5] * 1024]
        mock_response.total_tokens = 10

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            result = embedder.embed_query("find authentication code")

            assert len(result) == 1024
            mock_client.embed.assert_called_once_with(
                texts=["find authentication code"],
                model="voyage-code-3",
                input_type="query",
            )

    def test_cost_warning_at_5_dollar_threshold(self) -> None:
        """Should log warning when cost exceeds $5 threshold."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024]
        # Voyage Code 3 = $0.18/1M tokens, so $5 = ~27.8M tokens
        mock_response.total_tokens = 28_000_000

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            embedder.embed_documents(["doc1"])

            assert embedder.total_tokens_used == 28_000_000
            assert embedder.estimated_cost_usd > 5.0
            assert embedder.cost_warning_5_fired

    def test_cost_warning_at_10_dollar_threshold(self) -> None:
        """Should log warning when cost exceeds $10 threshold."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024]
        mock_response.total_tokens = 56_000_000  # ~$10.08

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            embedder.embed_documents(["doc1"])

            assert embedder.estimated_cost_usd > 10.0
            assert embedder.cost_warning_10_fired

    def test_cost_calculation_accuracy(self) -> None:
        """Should calculate cost accurately at Voyage Code 3 pricing."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024]
        mock_response.total_tokens = 1_000_000  # exactly 1M tokens

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.return_value = mock_response
            mock_client_class.return_value = mock_client

            embedder = VoyageEmbedder(api_key="test-key")
            embedder.embed_documents(["doc1"])

            # $0.18 per 1M tokens
            assert abs(embedder.estimated_cost_usd - 0.18) < 0.001

    def test_retry_on_transient_failure_then_success(self) -> None:
        """Should retry on transient failures and succeed."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1] * 1024]
        mock_response.total_tokens = 50

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            # First 2 calls fail, 3rd succeeds
            mock_client.embed.side_effect = [
                ConnectionError("network error"),
                ConnectionError("network error"),
                mock_response,
            ]
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):  # Don't actually sleep in tests
                embedder = VoyageEmbedder(api_key="test-key")
                result = embedder.embed_documents(["doc1"])

                assert len(result.embeddings) == 1
                assert mock_client.embed.call_count == 3

    def test_permanent_failure_after_max_retries(self) -> None:
        """Should raise after exhausting all retries."""
        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.side_effect = ConnectionError("permanent failure")
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):
                embedder = VoyageEmbedder(api_key="test-key")
                with pytest.raises(ConnectionError, match="permanent failure"):
                    embedder.embed_documents(["doc1"])

                # Should have tried MAX_RETRIES (5) times
                assert mock_client.embed.call_count == 5

    def test_embed_query_retry_on_transient_failure(self) -> None:
        """Should retry embed_query on transient failures."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.5] * 1024]
        mock_response.total_tokens = 10

        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.side_effect = [
                RuntimeError("transient"),
                mock_response,
            ]
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):
                embedder = VoyageEmbedder(api_key="test-key")
                result = embedder.embed_query("test query")

                assert len(result) == 1024
                assert mock_client.embed.call_count == 2

    def test_embed_query_permanent_failure(self) -> None:
        """Should raise after exhausting retries on embed_query."""
        with patch("voyageai.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.embed.side_effect = RuntimeError("permanent")
            mock_client_class.return_value = mock_client

            with patch("time.sleep"):
                embedder = VoyageEmbedder(api_key="test-key")
                with pytest.raises(RuntimeError, match="permanent"):
                    embedder.embed_query("test query")

                assert mock_client.embed.call_count == 5


class TestEmbeddingResult:
    """Tests for EmbeddingResult dataclass."""

    def test_creation(self) -> None:
        """Should create result with all fields."""
        result = EmbeddingResult(
            embeddings=[[0.1, 0.2]],
            token_usage=100,
            model="voyage-code-3",
        )
        assert result.embeddings == [[0.1, 0.2]]
        assert result.token_usage == 100
        assert result.model == "voyage-code-3"
