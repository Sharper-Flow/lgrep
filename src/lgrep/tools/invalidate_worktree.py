"""Invalidate worktree-specific cache entries.

For each worktree path:
1. Compute ``get_project_db_path(path)`` to find the cache dir.
2. Read ``project_meta.json`` and remove the path from ``alias_paths``.
3. If ``alias_paths`` is empty AND the canonical ``project_path`` is gone
   from the filesystem, delete the entire cache dir.
4. If the canonical exists, just update the meta — keep the cache.

Security guards (same pattern as ``prune_orphans``):
- Path confinement: resolved cache dir must be under ``LGREP_CACHE_DIR``.
- Refuse symlinks (TOCTOU guard).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import structlog

from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta
from lgrep.storage._chunk_store import DEFAULT_CACHE_DIR
from lgrep.tools._meta import make_meta

log = structlog.get_logger()


def _is_under(candidate: Path, root: Path) -> bool:
    """Return True when *candidate* is *root* or a descendant of it."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_cache_dir(cache_dir: Path | None = None) -> Path:
    """Resolve cache directory, preferring explicit arg over env var."""
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get("LGREP_CACHE_DIR", str(DEFAULT_CACHE_DIR)))


def _dir_size(path: Path) -> int:
    """Sum of root-path and descendant-path stat sizes (best-effort).

    Uses ``lstat`` to avoid following symlinks out of the cache tree.
    """
    total = 0
    if not path.exists():
        return 0
    try:
        total += path.lstat().st_size
    except OSError:
        return 0
    for child in path.rglob("*"):
        try:
            total += child.lstat().st_size
        except OSError:
            continue
    return total


def invalidate_worktree_cache(
    paths: list[str],
    cache_dir: Path | None = None,
) -> tuple[list[dict], int, int]:
    """Invalidate worktree-specific cache entries.

    Args:
        paths: List of worktree paths to invalidate.
        cache_dir: Override for the cache root directory.

    Returns:
        Tuple of (entries, paths_cleaned, bytes_reclaimed) where entries
        is a list of dicts matching ``WorktreeInvalidationEntry`` shape.
    """
    import time

    t0 = time.monotonic()
    root = _resolve_cache_dir(cache_dir)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root

    entries: list[dict] = []
    paths_cleaned = 0
    bytes_reclaimed = 0

    for raw_path in paths:
        path = Path(raw_path)

        # Symlink guard — refuse symlinked paths to prevent TOCTOU attacks
        try:
            if path.is_symlink():
                entries.append(
                    {
                        "path": raw_path,
                        "cache_dir": "",
                        "alias_removed": False,
                        "cache_deleted": False,
                        "bytes_reclaimed": 0,
                        "error": "refused: path is a symlink",
                    }
                )
                continue
        except OSError as exc:
            entries.append(
                {
                    "path": raw_path,
                    "cache_dir": "",
                    "alias_removed": False,
                    "cache_deleted": False,
                    "bytes_reclaimed": 0,
                    "error": str(exc),
                }
            )
            continue

        # Compute the cache dir for this path
        db_path = get_project_db_path(raw_path)

        # Path confinement: resolved cache dir must be under the cache root
        try:
            db_resolved = db_path.resolve()
        except OSError as exc:
            entries.append(
                {
                    "path": raw_path,
                    "cache_dir": str(db_path),
                    "alias_removed": False,
                    "cache_deleted": False,
                    "bytes_reclaimed": 0,
                    "error": str(exc),
                }
            )
            continue

        if not _is_under(db_resolved, root_resolved):
            entries.append(
                {
                    "path": raw_path,
                    "cache_dir": str(db_path),
                    "alias_removed": False,
                    "cache_deleted": False,
                    "bytes_reclaimed": 0,
                    "error": "refused: cache dir is outside LGREP_CACHE_DIR",
                }
            )
            continue

        # If cache dir doesn't exist, nothing to invalidate
        if not db_path.is_dir():
            entries.append(
                {
                    "path": raw_path,
                    "cache_dir": str(db_path),
                    "alias_removed": False,
                    "cache_deleted": False,
                    "bytes_reclaimed": 0,
                    "error": "no cache dir found for path",
                }
            )
            continue

        # Read current meta
        resolved_path_str = str(path.resolve())
        meta = read_project_meta(db_path)

        if meta is None:
            entries.append(
                {
                    "path": raw_path,
                    "cache_dir": str(db_path),
                    "alias_removed": False,
                    "cache_deleted": False,
                    "bytes_reclaimed": 0,
                    "error": "no project_meta.json found",
                }
            )
            continue

        # Remove the worktree path from alias_paths
        aliases: list[str] = list(meta.get("alias_paths", []))
        alias_removed = resolved_path_str in aliases
        if alias_removed:
            aliases.remove(resolved_path_str)

        # Check if canonical project_path still exists
        canonical_path = meta.get("project_path", "")
        canonical_exists = False
        if canonical_path:
            try:
                canonical_exists = Path(canonical_path).is_dir()
            except OSError:
                canonical_exists = False

        # Decide whether to delete the entire cache dir
        cache_deleted = False
        reclaimed = 0

        if not canonical_exists and len(aliases) == 0:
            # No canonical, no aliases — safe to delete the cache
            reclaimed = _dir_size(db_path)
            try:
                shutil.rmtree(db_path)
                cache_deleted = True
                bytes_reclaimed += reclaimed
                paths_cleaned += 1
            except OSError as exc:
                entries.append(
                    {
                        "path": raw_path,
                        "cache_dir": str(db_path),
                        "alias_removed": alias_removed,
                        "cache_deleted": False,
                        "bytes_reclaimed": 0,
                        "error": f"failed to delete cache dir: {exc}",
                    }
                )
                continue
        else:
            # Canonical exists or other aliases remain — just update meta
            # Re-write meta without the removed alias
            if alias_removed:
                # Write updated meta: project_path stays, aliases updated
                meta_path = db_path / "project_meta.json"
                import json

                updated_meta = {
                    "project_path": meta["project_path"],
                    "updated_at": meta.get("updated_at", time.time()),
                }
                if aliases:
                    updated_meta["alias_paths"] = aliases
                try:
                    tmp_path = meta_path.with_suffix(".tmp")
                    tmp_path.write_text(json.dumps(updated_meta), encoding="utf-8")
                    tmp_path.rename(meta_path)
                except OSError as exc:
                    entries.append(
                        {
                            "path": raw_path,
                            "cache_dir": str(db_path),
                            "alias_removed": False,
                            "cache_deleted": False,
                            "bytes_reclaimed": 0,
                            "error": f"failed to update meta: {exc}",
                        }
                    )
                    continue
            paths_cleaned += 1

        entries.append(
            {
                "path": raw_path,
                "cache_dir": str(db_path),
                "alias_removed": alias_removed,
                "cache_deleted": cache_deleted,
                "bytes_reclaimed": reclaimed,
                "error": None,
            }
        )

    log.info(
        "invalidate_worktree_cache_completed",
        paths_requested=len(paths),
        paths_cleaned=paths_cleaned,
        bytes_reclaimed=bytes_reclaimed,
    )

    return entries, paths_cleaned, bytes_reclaimed
