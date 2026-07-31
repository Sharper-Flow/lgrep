"""Root conftest.py — ensures the worktree src/ takes precedence over any
installed lgrep package when running tests from this worktree."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Insert worktree src/ at the front of sys.path so imports resolve to the
# worktree version of lgrep, not the globally installed one.
_src = str(Path(__file__).parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


class FakeClock:
    """Deterministic, manually advancing perf_counter seam for tests.

    The clock is shared between an Indexer and its slow stubs so that budget
    exhaustion and convergence can be tested without real sleeps or runner-
    dependent elapsed-time thresholds.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += seconds
        return self._now


@pytest.fixture
def fake_clock():
    """Provide a fresh deterministic advancing clock for each test."""
    return FakeClock()
