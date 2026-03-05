"""Shared _meta envelope helpers for lgrep symbol tools."""

from __future__ import annotations

import time

from lgrep.storage.token_tracker import TokenTracker

_TRACKER = TokenTracker()


def make_meta(start_time: float, tokens_saved: int = 0) -> dict:
    """Build a _meta envelope dict.

    Args:
        start_time: time.monotonic() value at the start of the operation
        tokens_saved: estimated tokens saved by this operation

    Returns:
        Dict with timing and token-savings fields, including persistent totals
    """
    elapsed_ms = (time.monotonic() - start_time) * 1000
    _TRACKER.record_savings(tokens_saved)
    _TRACKER.flush()

    tracker_meta = _TRACKER.meta()
    return {
        "timing_ms": round(elapsed_ms, 2),
        "tokens_saved": tokens_saved,
        "session_tokens": tracker_meta["session_tokens"],
        "total_tokens": tracker_meta["total_tokens"],
        "cost_avoided_usd": tracker_meta["cost_avoided_usd"],
    }


def error_response(message: str, **extra) -> dict:
    """Return a structured error response dict."""
    return {"error": message, **extra}
