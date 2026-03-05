"""Token savings tracker for lgrep symbol engine.

Tracks cumulative token savings from using symbol lookups instead of
reading full file contents. Persists total across sessions.

Usage in _meta envelopes:
    tracker = TokenTracker(storage_path=Path("~/.cache/lgrep/tokens.json"))
    tracker.record_savings(estimate_savings(symbols=5))
    meta = tracker.meta()  # {"session_tokens": 500, "total_tokens": 12000, "cost_avoided_usd": 0.024}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

log = structlog.get_logger()

# Claude 3.5 Sonnet input token cost: $3 per 1M tokens
# Used as the reference cost for "tokens avoided" calculations.
_COST_PER_TOKEN_USD = 3.0 / 1_000_000


def estimate_savings(symbols: int, avg_tokens_per_symbol: int = 150) -> int:
    """Estimate token savings from returning symbol metadata instead of full file content.

    Args:
        symbols: Number of symbols returned in the response
        avg_tokens_per_symbol: Average tokens per symbol definition (default: 150)

    Returns:
        Estimated tokens saved (int)
    """
    return symbols * avg_tokens_per_symbol


def cost_avoided(tokens: int) -> float:
    """Calculate the dollar cost avoided by saving the given number of tokens.

    Uses Claude 3.5 Sonnet input pricing ($3/1M tokens) as the reference.

    Args:
        tokens: Number of tokens saved

    Returns:
        Dollar amount avoided (float)
    """
    return tokens * _COST_PER_TOKEN_USD


class TokenTracker:
    """Persistent cumulative token savings ledger.

    Tracks:
    - session_tokens: tokens saved in the current process session (resets on restart)
    - total_tokens: cumulative tokens saved across all sessions (persisted to disk)

    Thread safety: not thread-safe; intended for single-threaded use within a tool call.
    """

    def __init__(self, storage_path: Path | str | None = None) -> None:
        """Initialize the token tracker.

        Args:
            storage_path: Path to the JSON persistence file. If None, uses a
                          default location under ~/.cache/lgrep/. If the file
                          doesn't exist, starts with zero totals.
        """
        if storage_path is None:
            cache_dir = Path.home() / ".cache" / "lgrep"
            storage_path = cache_dir / "token_savings.json"

        self._path = Path(storage_path)
        self.session_tokens: int = 0
        self.total_tokens: int = self._load_total()

    def _load_total(self) -> int:
        """Load persisted total from disk. Returns 0 if file missing or corrupt."""
        if not self._path.exists():
            return 0
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return int(data.get("total_tokens", 0))
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.warning("token_tracker_load_failed", path=str(self._path), error=str(e))
            return 0

    def record_savings(self, tokens: int) -> None:
        """Record token savings for the current session and total.

        Args:
            tokens: Number of tokens saved by this operation
        """
        self.session_tokens += tokens
        self.total_tokens += tokens

    def flush(self) -> None:
        """Persist the current total to disk atomically.

        Uses write-to-temp + rename for atomicity.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            data = {"total_tokens": self.total_tokens}
            tmp_path.write_text(json.dumps(data), encoding="utf-8")
            tmp_path.rename(self._path)
        except OSError as e:
            log.warning("token_tracker_flush_failed", path=str(self._path), error=str(e))

    def meta(self) -> dict:
        """Return a _meta envelope dict with token savings information.

        Returns:
            Dict with session_tokens, total_tokens, and cost_avoided_usd fields.
        """
        return {
            "session_tokens": self.session_tokens,
            "total_tokens": self.total_tokens,
            "cost_avoided_usd": round(cost_avoided(self.total_tokens), 6),
        }
