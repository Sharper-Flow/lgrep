import contextlib
import hashlib
import json
import os
import time as _time
from pathlib import Path

import pytest

from lgrep.storage import get_project_db_path
from lgrep.tools.prune_orphans import find_orphans, prune_orphans


def _hash_name(label: str) -> str:
    """Produce a 12-hex cache-dir name deterministically from a label."""
    return hashlib.sha256(label.encode()).hexdigest()[:12]


def _age(path: Path, seconds_ago: int) -> None:
    """Backdate ``path`` and descendants so they sit outside the grace window."""
    past = _time.time() - seconds_ago
    with contextlib.suppress(OSError):
        os.utime(path, (past, past))
    for child in path.rglob("*"):
        with contextlib.suppress(OSError):
            os.utime(child, (past, past))


def _make_cache_dir(
    cache_root: Path,
    name: str,
    *,
    project_path: Path | None = None,
    with_meta: bool = True,
    with_chunks: bool = True,
    corrupt_meta: bool = False,
    age_seconds: int | None = None,
) -> Path:
    cache_dir = cache_root / _hash_name(name)
    cache_dir.mkdir(parents=True)
    if with_chunks:
        (cache_dir / "chunks.lance").mkdir()
    if with_meta:
        meta_path = cache_dir / "project_meta.json"
        if corrupt_meta:
            meta_path.write_text("{not-json", encoding="utf-8")
        else:
            meta_path.write_text(
                json.dumps(
                    {
                        "project_path": str((project_path or cache_dir / "missing-project").resolve()),
                        "updated_at": 123.456,
                    }
                ),
                encoding="utf-8",
            )
    if age_seconds is not None:
        _age(cache_dir, age_seconds)
    return cache_dir


@pytest.fixture(autouse=True)
def _disable_grace_by_default(monkeypatch):
    """Default grace window to 0 in prune tests so existing fixtures keep
    reporting orphans. Tests that exercise the grace behavior override
    ``LGREP_PRUNE_MIN_AGE_S`` themselves.
    """
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "0")


def test_find_orphans_detects_missing_meta(tmp_path):
    _make_cache_dir(tmp_path, "missing-meta", with_meta=False, with_chunks=True)

    results = find_orphans(cache_dir=tmp_path)

    assert any(entry["reason"] == "missing_meta" for entry in results)


def test_find_orphans_detects_unreadable_meta(tmp_path):
    _make_cache_dir(tmp_path, "bad-meta", with_meta=True, with_chunks=True, corrupt_meta=True)

    results = find_orphans(cache_dir=tmp_path)

    # Corrupt JSON must be reported as `unreadable_meta`, not `missing_meta`,
    # and without surfacing a bogus project_path.
    assert any(
        entry["reason"] == "unreadable_meta" and entry["project_path"] is None
        for entry in results
    )


def test_find_orphans_detects_missing_chunks_lance(tmp_path):
    project_path = tmp_path / "project"
    project_path.mkdir()
    _make_cache_dir(tmp_path, "missing-chunks", project_path=project_path, with_meta=True, with_chunks=False)

    results = find_orphans(cache_dir=tmp_path)

    assert any(entry["reason"] == "missing_chunks_lance" for entry in results)


def test_find_orphans_detects_project_path_enoent(tmp_path):
    missing_project = tmp_path / "gone-project"
    _make_cache_dir(tmp_path, "gone", project_path=missing_project, with_meta=True, with_chunks=True)

    results = find_orphans(cache_dir=tmp_path)

    assert any(entry["reason"] == "project_path_enoent" for entry in results)


def test_find_orphans_skips_symbols_subdir(tmp_path):
    symbols_dir = tmp_path / "symbols"
    symbols_dir.mkdir()
    (symbols_dir / "index_deadbeef.json").write_text("{broken", encoding="utf-8")

    results = find_orphans(cache_dir=tmp_path)

    assert results == []


def test_find_orphans_skips_active_in_memory(tmp_path, monkeypatch):
    project_path = tmp_path / "live-project"
    project_path.mkdir()
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path))
    cache_dir = get_project_db_path(project_path)
    cache_dir.mkdir(parents=True)
    (cache_dir / "chunks.lance").mkdir()

    report = prune_orphans(cache_dir=tmp_path, active_set=[str(project_path)], dry_run=True)

    assert str(project_path.resolve()) in report["skipped_active"]
    assert report["orphans"] == []


def test_find_orphans_transient_eacces_preserved(tmp_path, monkeypatch):
    project_path = tmp_path / "permission-project"
    project_path.mkdir()
    _make_cache_dir(tmp_path, "permission-cache", project_path=project_path, with_meta=True, with_chunks=True)

    original_is_dir = Path.is_dir

    def flaky_is_dir(path_obj: Path):
        if path_obj == project_path.resolve():
            raise PermissionError("permission denied")
        return original_is_dir(path_obj)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert report["orphans"] == []


def test_prune_dry_run_does_not_delete(tmp_path):
    valid_project = tmp_path / "valid-project"
    valid_project.mkdir()
    orphan_dir = _make_cache_dir(tmp_path, "orphan", with_meta=False, with_chunks=True)
    _make_cache_dir(tmp_path, "valid", project_path=valid_project, with_meta=True, with_chunks=True)

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert orphan_dir.exists()
    assert any(entry["path"] == str(orphan_dir) and entry["bytes"] > 0 for entry in report["orphans"])
    assert report["deleted_dirs"] == 0
    # dry-run surfaces projected reclaim so the operator can preview
    # savings; the actual disk state is unchanged (orphan_dir still exists).
    assert report["reclaimed_bytes"] == sum(entry["bytes"] for entry in report["orphans"])
    # Both the orphan and the valid cache dir are readable, so dirs_examined
    # should reflect the full cache-root population we created.
    assert report["dirs_examined"] >= 2


def test_prune_execute_deletes_only_orphans_and_matches_dry_run_bytes(tmp_path):
    valid_project = tmp_path / "valid-project"
    valid_project.mkdir()
    orphan_dir = _make_cache_dir(tmp_path, "orphan-exec", with_meta=False, with_chunks=True)
    valid_dir = _make_cache_dir(tmp_path, "valid-exec", project_path=valid_project, with_meta=True, with_chunks=True)

    dry_run = prune_orphans(cache_dir=tmp_path, dry_run=True)
    execute = prune_orphans(cache_dir=tmp_path, dry_run=False)

    assert not orphan_dir.exists()
    assert valid_dir.exists()
    assert execute["reclaimed_bytes"] == sum(entry["bytes"] for entry in dry_run["orphans"])
    assert execute["deleted_dirs"] == len(dry_run["orphans"])


def test_prune_execute_continues_on_rmtree_failure(tmp_path, monkeypatch):
    first = _make_cache_dir(tmp_path, "orphan-fail", with_meta=False, with_chunks=True)
    second = _make_cache_dir(tmp_path, "orphan-ok", with_meta=False, with_chunks=True)

    import shutil

    original_rmtree = shutil.rmtree

    def flaky_rmtree(path, *args, **kwargs):
        if Path(path) == first:
            raise PermissionError("blocked")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)

    report = prune_orphans(cache_dir=tmp_path, dry_run=False)

    assert first.exists()
    assert not second.exists()
    assert any(entry["path"] == str(first) for entry in report["failures"])


def test_prune_execute_refuses_symlink_orphan(tmp_path):
    # TOCTOU guard: a hash-shaped cache entry could be a symlink pointing
    # at an unrelated tree. find_orphans must skip symlinks entirely so
    # execute never resolves through them, and the decoy target must be
    # left untouched.
    decoy = tmp_path / "decoy-target"
    decoy.mkdir()
    (decoy / "guard.txt").write_text("keep me", encoding="utf-8")

    orphan_link = tmp_path / _hash_name("symlink-cache")
    orphan_link.symlink_to(decoy, target_is_directory=True)

    report = prune_orphans(cache_dir=tmp_path, dry_run=False)

    # Symlink is silently skipped (no orphan entry, no failure entry).
    assert decoy.exists()
    assert (decoy / "guard.txt").exists()
    assert not any(entry["path"] == str(orphan_link) for entry in report["orphans"])


def test_prune_dry_run_projects_reclaimed_bytes(tmp_path):
    # Review LOG-3: dry-run must report projected reclaim (sum of orphan
    # bytes) so operators see savings before committing to --execute.
    orphan_dir = _make_cache_dir(
        tmp_path, "dry-bytes", with_meta=False, with_chunks=True
    )
    (orphan_dir / "chunks.lance" / "data.bin").write_bytes(b"x" * 1024)

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    projected = sum(entry["bytes"] for entry in report["orphans"])
    assert projected > 0
    assert report["reclaimed_bytes"] == projected
    assert report["deleted_dirs"] == 0


def test_prune_execute_refuses_path_outside_cache_root(tmp_path, monkeypatch):
    # Review SEC-1: even if find_orphans returns a path that escapes the
    # cache root (e.g., via post-scan tamper), prune_orphans refuses
    # rmtree. Escape target must be left untouched.
    escape_target = tmp_path / "escape-target"
    escape_target.mkdir()
    (escape_target / "guard.txt").write_text("safe", encoding="utf-8")

    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    orphan_dir = _make_cache_dir(cache_root, "escaping", with_meta=False, with_chunks=True)

    from lgrep.tools import prune_orphans as module

    def tampered(cache_dir, active_set=(), grace_seconds=None):
        return [
            {
                "path": str(escape_target),
                "reason": "missing_meta",
                "bytes": 1024,
                "project_path": None,
            }
        ]

    monkeypatch.setattr(module, "find_orphans", tampered)

    report = prune_orphans(cache_dir=cache_root, dry_run=False)

    assert escape_target.exists()
    assert (escape_target / "guard.txt").exists()
    assert report["deleted_dirs"] == 0
    assert any("outside cache root" in entry["error"] for entry in report["failures"])
    # Cache-root-local orphan is untouched because it was not in the
    # tampered list (the test covers only the confinement path).
    assert orphan_dir.exists()


def test_prune_skips_recently_modified_cache(tmp_path, monkeypatch):
    # Review SEC-5: unreadable_meta and missing_chunks_lance can be
    # caused by a still-writing indexer. Grace-period guard must
    # preserve very young caches.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    fresh_orphan = _make_cache_dir(
        tmp_path,
        "fresh-bad-meta",
        with_meta=True,
        with_chunks=True,
        corrupt_meta=True,
        age_seconds=None,
    )

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert fresh_orphan.exists()
    # Young unreadable_meta is held back until grace expires.
    assert not any(entry["path"] == str(fresh_orphan) for entry in report["orphans"])


def test_prune_reports_old_unreadable_meta(tmp_path, monkeypatch):
    # Complement to the grace test: once the cache is older than the
    # grace window, unreadable_meta is reported.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    old_orphan = _make_cache_dir(
        tmp_path,
        "old-bad-meta",
        with_meta=True,
        with_chunks=True,
        corrupt_meta=True,
        age_seconds=24 * 3600,
    )

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert any(entry["path"] == str(old_orphan) for entry in report["orphans"])


def test_prune_reports_missing_meta_immediately(tmp_path, monkeypatch):
    # Complement: missing_meta is an unambiguous orphan reason, so the
    # grace guard must NOT apply to it.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    fresh_orphan = _make_cache_dir(
        tmp_path,
        "fresh-missing-meta",
        with_meta=False,
        with_chunks=True,
        age_seconds=None,
    )

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert any(entry["path"] == str(fresh_orphan) for entry in report["orphans"])


def test_prune_dirs_examined_counts_all_attempted(tmp_path):
    # Review LOG-5: dirs_examined should count cache-shaped children
    # (matching the find_orphans filter) so the number is meaningful
    # against the cache contents the user can inspect.
    _make_cache_dir(tmp_path, "one", with_meta=False, with_chunks=True)
    _make_cache_dir(
        tmp_path, "two", project_path=tmp_path / "also", with_meta=True, with_chunks=True
    )

    # A non-hash-shaped directory must NOT be counted.
    (tmp_path / "unrelated").mkdir()

    report = prune_orphans(cache_dir=tmp_path, dry_run=True)

    assert report["dirs_examined"] == 2



