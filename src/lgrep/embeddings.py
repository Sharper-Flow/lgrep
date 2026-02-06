"""Voyage AI embedding client for lgrep.

Uses voyage-code-3 model for code-optimized embeddings.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

import structlog
import voyageai

log = structlog.get_logger()

# Voyage Code 3 specifications
MODEL_NAME = "voyage-code-3"
DEFAULT_DIMENSIONS = 1024  # Matryoshka: 256-2048
MAX_BATCH_SIZE = 128
MAX_RETRIES = 5
BASE_DELAY = 1.0

# Voyage Code 3 pricing: $0.18 per 1M tokens
COST_PER_MILLION_TOKENS = 0.18
COST_THRESHOLD_5 = 5.0
COST_THRESHOLD_10 = 10.0


@dataclass
class EmbeddingResult:
    """Result from embedding operation."""

    embeddings: list[list[float]]
    token_usage: int
    model: str


class VoyageEmbedder:
    """Voyage AI embedding client for code search.

    Uses voyage-code-3 model which achieves 92% retrieval quality on code benchmarks.
    Includes retry logic with exponential backoff and batching.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize the Voyage client.

        Args:
            api_key: Voyage API key. If not provided, uses VOYAGE_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self.api_key:
            raise ValueError("Voyage API key required. Set VOYAGE_API_KEY env var or pass api_key.")

        self.client = voyageai.Client(api_key=self.api_key)
        self.model = MODEL_NAME
        self.total_tokens_used = 0
        self.cost_warning_5_fired = False
        self.cost_warning_10_fired = False
        log.info("voyage_client_initialized", model=self.model)

    @property
    def estimated_cost_usd(self) -> float:
        """Estimated cost in USD based on tokens used."""
        return (self.total_tokens_used / 1_000_000) * COST_PER_MILLION_TOKENS

    def _check_cost_thresholds(self) -> None:
        """Check if cost thresholds have been exceeded and log warnings."""
        cost = self.estimated_cost_usd
        if cost >= COST_THRESHOLD_10 and not self.cost_warning_10_fired:
            self.cost_warning_10_fired = True
            log.warning(
                "voyage_cost_threshold_exceeded",
                threshold="$10",
                estimated_cost=f"${cost:.2f}",
                total_tokens=self.total_tokens_used,
            )
        elif cost >= COST_THRESHOLD_5 and not self.cost_warning_5_fired:
            self.cost_warning_5_fired = True
            log.warning(
                "voyage_cost_threshold_exceeded",
                threshold="$5",
                estimated_cost=f"${cost:.2f}",
                total_tokens=self.total_tokens_used,
            )

    def _embed_batch_with_retry(
        self, batch: list[str], input_type: str
    ) -> tuple[list[list[float]], int]:
        """Embed a single batch with exponential backoff retry.

        Args:
            batch: List of text strings to embed
            input_type: Voyage input type ("document" or "query")

        Returns:
            Tuple of (embeddings, token_usage)

        Raises:
            Exception: After MAX_RETRIES failed attempts
        """
        for attempt in range(MAX_RETRIES):
            try:
                result = self.client.embed(
                    texts=batch,
                    model=self.model,
                    input_type=input_type,
                )
                return result.embeddings, result.total_tokens
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    log.error(
                        "voyage_batch_failed_permanent",
                        input_type=input_type,
                        error=str(e),
                    )
                    raise

                delay = BASE_DELAY * (2**attempt) + random.uniform(0, 1)
                log.warning(
                    "voyage_batch_failed_retrying",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(e),
                )
                time.sleep(delay)

        # Unreachable: the loop always returns or raises on the last attempt
        raise RuntimeError("Unexpected end of retry loop")  # pragma: no cover

    def embed_documents(
        self,
        texts: list[str],
        batch_size: int = MAX_BATCH_SIZE,
    ) -> EmbeddingResult:
        """Embed a list of documents (code chunks) with retry logic.

        Args:
            texts: List of text strings to embed
            batch_size: Number of texts per API call (max 128)

        Returns:
            EmbeddingResult with embeddings and token usage
        """
        if not texts:
            return EmbeddingResult(embeddings=[], token_usage=0, model=self.model)

        all_embeddings: list[list[float]] = []
        total_tokens = 0

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            log.debug(
                "voyage_embed_batch",
                batch_num=i // batch_size + 1,
                batch_size=len(batch),
            )
            embeddings, tokens = self._embed_batch_with_retry(batch, "document")
            all_embeddings.extend(embeddings)
            total_tokens += tokens

        self.total_tokens_used += total_tokens
        self._check_cost_thresholds()
        log.info(
            "voyage_embed_complete",
            num_texts=len(texts),
            total_tokens=total_tokens,
            total_tokens_cumulative=self.total_tokens_used,
            estimated_cost_usd=f"${self.estimated_cost_usd:.4f}",
        )

        return EmbeddingResult(
            embeddings=all_embeddings,
            token_usage=total_tokens,
            model=self.model,
        )

    def embed_query(self, query: str) -> list[float]:
        """Embed a search query with retry logic.

        Args:
            query: Search query string

        Returns:
            Embedding vector (1024 dimensions)
        """
        log.debug("voyage_embed_query", query_len=len(query))

        embeddings, tokens = self._embed_batch_with_retry([query], "query")
        self.total_tokens_used += tokens
        self._check_cost_thresholds()
        log.debug("voyage_query_embedded", tokens=tokens)
        return embeddings[0]
