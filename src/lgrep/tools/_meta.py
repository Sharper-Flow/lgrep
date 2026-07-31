"""Shared _meta envelope helpers for lgrep symbol tools."""

from __future__ import annotations

import time

from lgrep.storage.token_tracker import TokenTracker

_TRACKER = TokenTracker()


def make_meta(start_time: float, tool: str, tokens_saved: int = 0) -> dict:
    """Build a canonical _meta envelope dict.

    Args:
        start_time: time.monotonic() value at the start of the operation
        tool: name of the producing MCP tool
        tokens_saved: estimated tokens saved by this operation

    Returns:
        Dict with timing, producing tool, and token-savings fields, including
        persistent totals. This is the single metadata producer used by every
        MCP response path.
    """
    elapsed_ms = (time.monotonic() - start_time) * 1000
    _TRACKER.record_savings(tokens_saved)
    _TRACKER.flush()

    tracker_meta = _TRACKER.meta()
    return {
        "tool": tool,
        "timing_ms": round(elapsed_ms, 2),
        "tokens_saved": tokens_saved,
        "session_tokens": tracker_meta["session_tokens"],
        "total_tokens": tracker_meta["total_tokens"],
        "cost_avoided_usd": tracker_meta["cost_avoided_usd"],
    }


def error_response(message: str, **extra) -> dict:
    """Return a structured error response dict."""
    return {"error": message, **extra}
