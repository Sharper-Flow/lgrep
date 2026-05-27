"""Conflict-marker guard: fail on unresolved git merge markers in tracked text files.

RED phase: test fails when CHANGELOG.md (or any tracked text file) contains
unresolved <<<<<<< / ======= / >>>>>>> markers.
GREEN phase: passes once all markers are resolved.
"""

import subprocess
from pathlib import Path

import pytest

# Paths that are known binary / generated / cache and should not be scanned.
SKIP_SUFFIXES = frozenset(
    [
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".lock",  # uv.lock, package-lock.json are generated
    ]
)

SKIP_NAME_PARTS = frozenset(
    [
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        ".git",
    ]
)

# Literal conflict-marker prefixes that git uses.
CONFLICT_PREFIXES = (b"<<<<<<< ", b"=======", b">>>>>>> ")


def _is_text_file(path: Path) -> bool:
    """Heuristic: skip known binary / generated / cache paths."""
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    # Skip paths that contain cache / generated directory names.
    return all(part not in SKIP_NAME_PARTS for part in path.parts)


def _conflict_marker_lines(data: bytes) -> list[int]:
    """Return 1-indexed line numbers that start with a git conflict marker."""
    matches: list[int] = []
    # Split on any newline variant; handle files without trailing newline.
    for line_number, line in enumerate(data.splitlines(), start=1):
        for prefix in CONFLICT_PREFIXES:
            if line.startswith(prefix):
                matches.append(line_number)
                break
    return matches


class TestConflictMarkers:
    """Tracked text files must not contain unresolved git conflict markers."""

    def test_no_conflict_markers_in_tracked_files(self):
        """Every tracked text file must be free of <<<<<<< / ======= / >>>>>>> markers."""
        repo_root = Path(__file__).resolve().parent.parent
        # Enumerate tracked files via git so we respect .gitignore.
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
        files = result.stdout.split(b"\x00")
        offenders = []
        for raw_path in files:
            if not raw_path:
                continue
            path = repo_root / raw_path.decode("utf-8", errors="surrogateescape")
            if not _is_text_file(path):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                # File may have been deleted since git ls-files ran.
                continue
            marker_lines = _conflict_marker_lines(data)
            offenders.extend(f"{path.relative_to(repo_root)}:{line}" for line in marker_lines)

        if offenders:
            formatted = "\n  ".join(offenders)
            pytest.fail(f"Unresolved git conflict markers found in tracked files:\n  {formatted}")
