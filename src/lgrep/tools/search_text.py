"""lgrep_search_text tool implementation.

Performs literal text search across source files in a repository.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import structlog

from lgrep.tools._meta import error_response
from lgrep.tools._paths import resolve_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

log = structlog.get_logger()

# Agents call search_text in bursts: most calls on a repository follow another
# within seconds. A complete file list is reused for this long, so files
# created or deleted inside the window are seen on the next walk.
_FILE_LIST_TTL_S = 5.0
_file_lists: dict[Path, tuple[float, list[Path]]] = {}
_file_lists_lock = threading.Lock()


class SearchProgress:
    """Publish a file's matches and count together for deadline snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: list[dict] = []
        self._files_searched = 0

    def record_file(self, matches: list[dict]) -> None:
        with self._lock:
            self._results.extend(matches)
            self._files_searched += 1

    def snapshot(self, *, truncated: bool, reason: str = "") -> dict:
        with self._lock:
            results = self._results.copy()
            files_searched = self._files_searched
        return {
            "results": results,
            "total_matches": len(results),
            "truncated": truncated,
            "truncation_reason": reason,
            "files_searched": files_searched,
        }


def _source_files(root: Path) -> Iterator[Path]:
    """Yield the repository's searchable files, from a fresh list if one exists.

    A walk is cached only when the caller consumes all of it, so a search
    that stops at ``max_results`` keeps its early exit.
    """
    from lgrep.discovery import FileDiscovery

    key = root.resolve()
    now = time.monotonic()
    with _file_lists_lock:
        cached = _file_lists.get(key)
    if cached is not None and now - cached[0] < _FILE_LIST_TTL_S:
        yield from cached[1]
        return
    files: list[Path] = []
    for file_path in FileDiscovery(root).find_files():
        files.append(file_path)
        yield file_path
    with _file_lists_lock:
        for stale in [k for k, (at, _) in _file_lists.items() if now - at >= _FILE_LIST_TTL_S]:
            del _file_lists[stale]
        _file_lists[key] = (now, files)


def _search_files(root: Path, named_file: Path | None) -> Iterator[Path]:
    """Yield the one named file, or the repository's searchable files.

    A named file is searched whatever the ignore rules say about it, since
    the caller asked for it by name. It keeps the size cap discovery applies
    to every searched file, so one call cannot read an unbounded file.
    """
    if named_file is None:
        yield from _source_files(root)
        return

    from lgrep.discovery import MAX_FILE_SIZE_BYTES

    try:
        if named_file.stat().st_size <= MAX_FILE_SIZE_BYTES:
            yield named_file
    except OSError:
        return


def search_text(
    query: str,
    repo_path: str,
    max_results: int = 50,
    case_sensitive: bool = False,
    *,
    deadline: float | None = None,
    cancel_event: threading.Event | None = None,
    progress: SearchProgress | None = None,
) -> dict:
    """Search for literal text in a repository, or in one named file.

    Args:
        query: Text to search for
        repo_path: Absolute path to the repository root, or to one file to
            search. A file is one unit of work and its matches carry the
            file's name as ``file_path``.
        max_results: Maximum number of results to return (default: 50)
        case_sensitive: Whether to perform case-sensitive matching (default: False)

    Returns:
        Dict with results list (file_path, line_number, line).
        Returns error dict if the path does not exist.
    """
    # Input validation
    if not query or not query.strip():
        return error_response("query must not be empty")

    target = resolve_path(repo_path, "file")
    if isinstance(target, dict):
        return target
    named_file = target if target.is_file() else None
    root = target.parent if named_file else target

    progress = progress if progress is not None else SearchProgress()
    remaining = max_results
    search_query = query if case_sensitive else query.lower()

    def stopped() -> str:
        if cancel_event is not None and cancel_event.is_set():
            return "cancelled"
        if deadline is not None and time.monotonic() >= deadline:
            return "deadline"
        return ""

    reason = stopped()
    if reason:
        return progress.snapshot(truncated=True, reason=reason)

    if remaining <= 0:
        return progress.snapshot(truncated=False)

    for file_path in _search_files(root, named_file):
        reason = stopped()
        if reason:
            return progress.snapshot(truncated=True, reason=reason)
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if search_query not in (text if case_sensitive else text.lower()):
            progress.record_file([])
            continue

        matches = []
        for line_num, line in enumerate(text.splitlines(), start=1):
            compare = line if case_sensitive else line.lower()
            if search_query in compare:
                try:
                    rel_path = str(file_path.relative_to(root))
                except ValueError:
                    rel_path = str(file_path)
                matches.append(
                    {
                        "file_path": rel_path,
                        "line_number": line_num,
                        "line": line.rstrip(),
                    }
                )
                if len(matches) >= remaining:
                    break
        progress.record_file(matches)
        remaining -= len(matches)
        if remaining == 0:
            break

    return progress.snapshot(truncated=False)
