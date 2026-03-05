"""Root conftest.py — ensures the worktree src/ takes precedence over any
installed lgrep package when running tests from this worktree."""

import sys
from pathlib import Path

# Insert worktree src/ at the front of sys.path so imports resolve to the
# worktree version of lgrep, not the globally installed one.
_src = str(Path(__file__).parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
