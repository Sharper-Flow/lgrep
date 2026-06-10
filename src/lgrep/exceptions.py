"""lgrep-owned exceptions shared across modules.

Lives in its own module so both ``lgrep.indexing`` and ``lgrep.embeddings``
can raise the same cancellation exception without a circular import
(``indexing`` imports ``embeddings``).
"""

from __future__ import annotations


class OperationCancelled(Exception):
    """Raised when a cooperative ``cancel_event`` is set during blocking work.

    Used by the daemon's bounded executor to release worker slots when the
    awaiting MCP coroutine is cancelled (e.g. 8s tool timeout). Checked at
    every blocking seam in the indexing/embedding path:

    - ``Indexer.index_all`` per-file loop (and its wall-clock backstop)
    - ``Indexer.index_file`` before the embed and storage steps
    - ``VoyageEmbedder.embed_documents`` between batches
    - ``VoyageEmbedder._embed_batch_with_retry`` before each attempt and
      during its exponential-backoff wait

    so a single slow file (or a long retry backoff) cannot hold the worker
    thread past cancellation.
    """
