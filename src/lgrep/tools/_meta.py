"""Shared _meta envelope helpers for lgrep tools."""

from __future__ import annotations

import time


def make_meta(start_time: float, tool: str) -> dict:
    """Build a canonical _meta envelope dict.

    Args:
        start_time: time.monotonic() value at the start of the operation
        tool: name of the producing MCP tool
    Returns:
        Dict with the producing tool and elapsed time.
    """
    elapsed_ms = (time.monotonic() - start_time) * 1000
    return {
        "tool": tool,
        "timing_ms": round(elapsed_ms, 2),
    }


def error_response(message: str, **extra) -> dict:
    """Return a structured error response dict."""
    return {"error": message, **extra}
