"""Shared _meta envelope helpers for lgrep symbol tools."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def make_meta(start_time: float, tokens_saved: int = 0) -> dict:
    """Build a _meta envelope dict.

    Args:
        start_time: time.monotonic() value at the start of the operation
        tokens_saved: estimated tokens saved by this operation

    Returns:
        Dict with timing_ms and tokens_saved fields
    """
    elapsed_ms = (time.monotonic() - start_time) * 1000
    return {
        "timing_ms": round(elapsed_ms, 2),
        "tokens_saved": tokens_saved,
    }


def error_response(message: str, **extra) -> dict:
    """Return a structured error response dict."""
    return {"error": message, **extra}
