"""First-use index bootstrap for the symbol query tools.

search_symbols, get_symbol, get_symbols, and search_references answer an
unindexed checkout with "Repository not indexed", which forces the caller
to run lgrep_index_symbols_folder first. Local git checkouts now build
their own index on the first query instead (``ensure_symbol_index``):

- The index is built under the checkout's own resolved-path key, so
  results always come from this checkout's files.
- When another checkout sharing the same git common directory (a linked
  worktree of the same repository) already has an index, the newest such
  index is copied as a seed and an incremental refresh re-parses only
  files whose content hash differs.
- With no indexed sibling, the first query runs a full index.

When a new index is created, indexes whose repo_path no longer exists on
disk are deleted. Deletion reuses ``prune_symbols`` (reason
``repo_path_enoent`` — grace-exempt, ``github:`` keys skipped) rather
than adding a second pruning mechanism.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from lgrep.storage.index_store import IndexStore, normalize_repo_key

log = structlog.get_logger()

# Matches the git invocation timeout in storage._chunk_store.canonical_repo_key.
_GIT_TIMEOUT_S = 2.0


def git_common_dir(repo_path: Path) -> str | None:
    """Return the absolute git common dir for *repo_path*, or None.

    Linked worktrees of one repository share a common dir (the trunk's
    ``.git``), so two checkouts are siblings iff this value matches.
    ``--path-format=absolute`` requires Git >= 2.30 (January 2021); an
    older git, a missing git binary, or a non-checkout path returns None
    and the caller keeps the "not indexed" error contract.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _newest_sibling_index(store: IndexStore, common_dir: str) -> str | None:
    """Return the repo path of the freshest indexed sibling checkout.

    Candidates come from the store's sidecars; a candidate must be a live
    local directory whose common dir matches. Gone directories cannot
    produce a common dir (git needs a cwd), so they drop out here and are
    pruned by the create-time sweep instead.
    """
    newest: tuple[str, float] | None = None
    for candidate in store.list_repos():
        if candidate.startswith("github:"):
            continue
        candidate_path = Path(candidate)
        try:
            if not candidate_path.is_dir():
                continue
        except OSError:
            continue
        if git_common_dir(candidate_path) != common_dir:
            continue
        indexed_at = store.last_indexed_at(candidate)
        if indexed_at is None:
            continue
        if newest is None or indexed_at > newest[1]:
            newest = (candidate, indexed_at)
    return newest[0] if newest else None


def ensure_symbol_index(repo_path: str, storage_dir: Path | str | None = None) -> bool:
    """Build an index for an unindexed local git checkout on first use.

    Seeds from the newest indexed sibling worktree when one exists, then
    refreshes incrementally; otherwise runs a full index. Returns True
    when an index now exists for the checkout. Never raises: a failed
    bootstrap returns False and the caller keeps the "not indexed"
    error. Non-git folders and ``github:`` keys are out of scope.
    """
    if repo_path.startswith("github:"):
        return False
    try:
        resolved = Path(repo_path).resolve()
    except OSError:
        return False
    if not resolved.is_dir():
        return False

    common_dir = git_common_dir(resolved)
    if common_dir is None:
        return False

    store = IndexStore(storage_dir=storage_dir)
    normalized = normalize_repo_key(str(resolved))
    seed_repo = _newest_sibling_index(store, common_dir)
    seeded = store.seed_from(seed_repo, normalized) if seed_repo else False

    from lgrep.tools.index_folder import index_folder

    result = index_folder(str(resolved), storage_dir=storage_dir, incremental=seeded)
    if "error" in result:
        log.warning("index_bootstrap_failed", repo=normalized, seeded=seeded, error=result["error"])
        return False

    # A new index exists, so sweep indexes whose checkout is gone. The
    # seeded copy left the target body carrying the seed's repo_path only
    # until the refresh above saved over it; by this point the body names
    # this checkout, so the sweep cannot misclassify it.
    from lgrep.tools.prune_symbols import prune_symbols

    prune_report = prune_symbols(
        dry_run=False,
        storage_dir=Path(storage_dir) if storage_dir is not None else None,
    )
    log.info(
        "index_bootstrapped",
        repo=normalized,
        seeded_from=seed_repo if seeded else None,
        pruned_gone_paths=len(prune_report["stale_indexes"]),
    )
    return True
