"""Tests for the symbol-store pruner.

Mirrors ``tests/test_prune_orphans.py`` shape but adapted to the
symbol-store file layout (single ``index_<hash16>.json`` per repo).

ACs covered here (owned by the core module):
- AC1: dry_run default reports stale candidates + projected reclaim
- AC2: execute deletes only stale entries, reclaimed_bytes == sum(stat().st_size)
- AC5: github: entries skipped upfront (never classified)
- AC6: symlinked index files refused at scan + delete time
- AC7: paths outside storage root refused at delete time
- AC9: per-entry unlink failures captured in failures[] and do not abort
- AC10: mtime within grace window protects unreadable_index_json only
        (repo_path_enoent and missing_repo_path_field bypass grace)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time as _time
from pathlib import Path

import pytest

from lgrep.tools.prune_symbols import find_stale_indexes, prune_symbols


def _key(label: str) -> str:
    """Produce a 16-hex index-file suffix deterministically from a label."""
    return hashlib.sha256(label.encode()).hexdigest()[:16]


def _age(path: Path, seconds_ago: int) -> None:
    """Backdate ``path`` so it sits outside the grace window."""
    past = _time.time() - seconds_ago
    with contextlib.suppress(OSError):
        os.utime(path, (past, past))


def _make_index(
    storage_root: Path,
    label: str,
    *,
    repo_path: str | None = "unset",
    files: dict | None = None,
    symbols: dict | None = None,
    version: str = "2.0",
    raw_body: str | None = None,
    age_seconds: int | None = None,
) -> Path:
    """Create an ``index_<hash16>.json`` file under ``storage_root``.

    If ``raw_body`` is set, it is written verbatim (used for malformed-JSON
    and missing-field fixtures). Otherwise a well-formed payload is built
    from ``repo_path``/``files``/``symbols``/``version``. If ``repo_path``
    is None and ``raw_body`` is None, the field is omitted entirely.
    """
    storage_root.mkdir(parents=True, exist_ok=True)
    index_file = storage_root / f"index_{_key(label)}.json"
    if raw_body is not None:
        index_file.write_text(raw_body, encoding="utf-8")
    else:
        payload: dict = {
            "files": files or {},
            "symbols": symbols or {},
            "version": version,
        }
        if repo_path != "unset":
            payload["repo_path"] = repo_path  # type: ignore[assignment]
        index_file.write_text(json.dumps(payload), encoding="utf-8")
    if age_seconds is not None:
        _age(index_file, age_seconds)
    return index_file


@pytest.fixture(autouse=True)
def _disable_grace_by_default(monkeypatch):
    """Default grace window to 0 so existing fixtures keep reporting stale.

    Tests that exercise grace override ``LGREP_PRUNE_MIN_AGE_S`` themselves.
    """
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "0")


# ---------------------------------------------------------------------------
# Classification (3 reasons)
# ---------------------------------------------------------------------------


def test_find_stale_detects_repo_path_enoent(tmp_path):
    # AC: repo_path points at a directory that no longer exists.
    missing_repo = tmp_path / "gone-repo"
    _make_index(tmp_path, "enoent", repo_path=str(missing_repo))

    results = find_stale_indexes(storage_dir=tmp_path)

    assert any(
        entry["reason"] == "repo_path_enoent" and entry["repo_path"] == str(missing_repo)
        for entry in results
    )


def test_find_stale_detects_unreadable_index_json(tmp_path):
    # AC: corrupt JSON is classified unreadable_index_json with no repo_path.
    _make_index(tmp_path, "bad-json", raw_body="{not-json")

    results = find_stale_indexes(storage_dir=tmp_path)

    assert any(
        entry["reason"] == "unreadable_index_json" and entry["repo_path"] is None
        for entry in results
    )


def test_find_stale_detects_missing_repo_path_field(tmp_path):
    # AC: well-formed JSON without a repo_path key is its own unambiguous reason.
    _make_index(tmp_path, "no-repo-field", repo_path=None)

    results = find_stale_indexes(storage_dir=tmp_path)

    assert any(entry["reason"] == "missing_repo_path_field" for entry in results)


# ---------------------------------------------------------------------------
# Skip non-local (AC5)
# ---------------------------------------------------------------------------


def test_find_stale_skips_github_entries(tmp_path):
    # AC5: github: keys have no local path to staleness-check and must be
    # skipped outright — never classified, never deleted.
    _make_index(
        tmp_path,
        "github-entry",
        repo_path="github:owner/name@main",
    )

    results = find_stale_indexes(storage_dir=tmp_path)

    assert results == []


# ---------------------------------------------------------------------------
# Live entries preserved
# ---------------------------------------------------------------------------


def test_find_stale_preserves_live_repo(tmp_path):
    live_repo = tmp_path / "live-repo"
    live_repo.mkdir()
    _make_index(tmp_path, "live", repo_path=str(live_repo))

    results = find_stale_indexes(storage_dir=tmp_path)

    assert results == []


def test_find_stale_preserves_existing_index_for_live_path_with_unreadable_json(
    tmp_path, monkeypatch
):
    # If the repo_path on disk is fine but the JSON is corrupt, stale reason
    # is still unreadable_index_json (grace may apply separately). This pins
    # the classification path: repo_path existence check only fires after
    # the JSON is successfully parsed.
    live_repo = tmp_path / "live-repo"
    live_repo.mkdir()
    _make_index(tmp_path, "live-bad-json", raw_body="{bad")

    results = find_stale_indexes(storage_dir=tmp_path)

    assert any(entry["reason"] == "unreadable_index_json" for entry in results)


# ---------------------------------------------------------------------------
# Active set
# ---------------------------------------------------------------------------


def test_prune_skips_active_set_entries(tmp_path):
    # Parity with prune_orphans.active_set: a repo_path matching an
    # in-memory active project is preserved even if its on-disk path
    # is gone (the active process owns it).
    repo_path_str = str(tmp_path / "active-repo")
    _make_index(tmp_path, "active", repo_path=repo_path_str)

    dry_run = prune_symbols(storage_dir=tmp_path, active_set=[repo_path_str], dry_run=True)

    assert dry_run["skipped_active"] == [repo_path_str]
    assert dry_run["stale_indexes"] == []


def test_prune_classifies_each_index_once(tmp_path, monkeypatch):
    """AC6: one classification pass serves active accounting and pruning."""
    from lgrep.tools import prune_symbols as module

    index_count = 3
    for index in range(index_count):
        _make_index(
            tmp_path,
            f"classification-count-{index}",
            repo_path=str(tmp_path / f"missing-repo-{index}"),
        )

    original_classify = module._classify
    classified_files = []

    def counting_classify(index_file):
        classified_files.append(index_file)
        return original_classify(index_file)

    monkeypatch.setattr(module, "_classify", counting_classify)

    report = module.prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert len(classified_files) == index_count
    assert len(report["stale_indexes"]) == index_count


# ---------------------------------------------------------------------------
# Symlink refusal (AC6) — scan and delete time
# ---------------------------------------------------------------------------


def test_find_stale_skips_symlinked_index(tmp_path):
    # AC6 (scan side): an index_*.json symlink could point anywhere.
    # find_stale_indexes must skip it entirely.
    decoy = tmp_path / "decoy-target"
    decoy.mkdir()
    (decoy / "payload.txt").write_text("keep me", encoding="utf-8")

    # A real index file outside the storage root, then symlinked in.
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {"repo_path": str(tmp_path / "missing"), "files": {}, "symbols": {}, "version": "2.0"}
        ),
        encoding="utf-8",
    )
    link = tmp_path / "index_deadbeefdeadbeef.json"
    link.symlink_to(outside)

    results = find_stale_indexes(storage_dir=tmp_path)

    assert not any(entry["path"] == str(link) for entry in results)


def test_prune_execute_refuses_symlink_at_delete_time(tmp_path, monkeypatch):
    # AC6 (delete side): even if a symlink slipped past the scan filter
    # (e.g., tampered list), execute must refuse to unlink through it.
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (outside / "guard.txt").write_text("safe", encoding="utf-8")

    storage = tmp_path / "symbols"
    storage.mkdir()
    link = storage / "index-feedfacefeedface.json"
    link.symlink_to(outside)

    from lgrep.tools import prune_symbols as module

    def tampered(storage_dir, active_set=(), grace_seconds=None):
        return [
            {
                "path": str(link),
                "reason": "repo_path_enoent",
                "bytes": 1024,
                "repo_path": str(outside),
            }
        ]

    monkeypatch.setattr(module, "find_stale_indexes", tampered)

    report = prune_symbols(storage_dir=storage, dry_run=False)

    assert outside.exists()
    assert (outside / "guard.txt").exists()
    assert any("symlink" in entry["error"] for entry in report["failures"])
    assert report["deleted_files"] == 0


# ---------------------------------------------------------------------------
# Path confinement (AC7)
# ---------------------------------------------------------------------------


def test_prune_execute_refuses_path_outside_storage_root(tmp_path, monkeypatch):
    # AC7: post-scan tamper must not let unlink escape the storage root.
    escape_target = tmp_path / "escape-target"
    escape_target.mkdir()
    (escape_target / "guard.txt").write_text("safe", encoding="utf-8")

    storage = tmp_path / "symbols"
    storage.mkdir()

    from lgrep.tools import prune_symbols as module

    def tampered(storage_dir, active_set=(), grace_seconds=None):
        return [
            {
                "path": str(escape_target),
                "reason": "repo_path_enoent",
                "bytes": 1024,
                "repo_path": None,
            }
        ]

    monkeypatch.setattr(module, "find_stale_indexes", tampered)

    report = prune_symbols(storage_dir=storage, dry_run=False)

    assert escape_target.exists()
    assert (escape_target / "guard.txt").exists()
    assert report["deleted_files"] == 0
    assert any("outside" in entry["error"] for entry in report["failures"])


# ---------------------------------------------------------------------------
# Dry-run default + projected bytes (AC1)
# ---------------------------------------------------------------------------


def test_prune_dry_run_does_not_delete_and_projects_bytes(tmp_path):
    # AC1: dry_run=True default; reclaimed_bytes == sum of stale bytes;
    # nothing actually unlinked.
    missing_repo = tmp_path / "gone"
    stale_file = _make_index(tmp_path, "dry", repo_path=str(missing_repo))

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert stale_file.exists()
    assert report["deleted_files"] == 0
    expected = stale_file.stat().st_size
    assert report["reclaimed_bytes"] == expected
    assert any(entry["path"] == str(stale_file) for entry in report["stale_indexes"])


def test_prune_default_dry_run_is_true(tmp_path):
    # AC: dry_run defaults to True — no flag / no kwarg ⇒ dry run.
    missing_repo = tmp_path / "default-gone"
    stale_file = _make_index(tmp_path, "default", repo_path=str(missing_repo))

    report = prune_symbols(storage_dir=tmp_path)

    assert report["dry_run"] is True
    assert stale_file.exists()


# ---------------------------------------------------------------------------
# Execute path (AC2) + per-entry failure isolation (AC9)
# ---------------------------------------------------------------------------


def test_prune_execute_deletes_only_stale_and_matches_dry_run_bytes(tmp_path):
    # AC2: execute deletes stale, preserves live; reclaimed_bytes ==
    # sum(stat().st_size) of deleted files; matches dry-run projection.
    live_repo = tmp_path / "live"
    live_repo.mkdir()
    stale_repo = tmp_path / "stale"

    stale_file = _make_index(tmp_path, "stale-exec", repo_path=str(stale_repo))
    live_file = _make_index(tmp_path, "live-exec", repo_path=str(live_repo))

    dry_run = prune_symbols(storage_dir=tmp_path, dry_run=True)
    execute = prune_symbols(storage_dir=tmp_path, dry_run=False)

    assert not stale_file.exists()
    assert live_file.exists()
    assert execute["deleted_files"] == len(dry_run["stale_indexes"])
    assert execute["reclaimed_bytes"] == sum(entry["bytes"] for entry in dry_run["stale_indexes"])


def test_prune_execute_continues_on_unlink_failure(tmp_path, monkeypatch):
    # AC9: per-entry unlink failures appear in failures[] and do not
    # abort the batch. Other stale entries still get deleted.
    stale_repo_a = tmp_path / "gone-a"
    stale_repo_b = tmp_path / "gone-b"
    first = _make_index(tmp_path, "fail-first", repo_path=str(stale_repo_a))
    second = _make_index(tmp_path, "fail-second", repo_path=str(stale_repo_b))

    original_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self == first:
            raise PermissionError("blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    report = prune_symbols(storage_dir=tmp_path, dry_run=False)

    assert first.exists()
    assert not second.exists()
    assert any(entry["path"] == str(first) for entry in report["failures"])
    assert report["deleted_files"] == 1


# ---------------------------------------------------------------------------
# Grace window (AC10)
# ---------------------------------------------------------------------------


def test_prune_grace_preserves_recent_unreadable_index(tmp_path, monkeypatch):
    # AC10: unreadable_index_json is grace-eligible. A fresh corrupt
    # file is held back; the operator can override with LGREP_PRUNE_MIN_AGE_S=0.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    fresh = _make_index(tmp_path, "fresh-bad", raw_body="{bad", age_seconds=None)

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert fresh.exists()
    assert not any(entry["path"] == str(fresh) for entry in report["stale_indexes"])


def test_prune_grace_reports_old_unreadable_index(tmp_path, monkeypatch):
    # AC10 complement: once older than the grace window, the same reason
    # is reported.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    old = _make_index(tmp_path, "old-bad", raw_body="{bad", age_seconds=24 * 3600)

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert any(entry["path"] == str(old) for entry in report["stale_indexes"])


def test_prune_grace_does_not_protect_repo_path_enoent(tmp_path, monkeypatch):
    # AC10: repo_path_enoent is grace-exempt — reported immediately even
    # when mtime is fresh.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    missing_repo = tmp_path / "fresh-missing"
    fresh = _make_index(
        tmp_path,
        "fresh-enoent",
        repo_path=str(missing_repo),
        age_seconds=None,
    )

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert any(
        entry["path"] == str(fresh) and entry["reason"] == "repo_path_enoent"
        for entry in report["stale_indexes"]
    )


def test_prune_grace_does_not_protect_missing_repo_path_field(tmp_path, monkeypatch):
    # AC10: missing_repo_path_field is grace-exempt.
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "3600")
    fresh = _make_index(
        tmp_path,
        "fresh-missing-field",
        repo_path=None,
        age_seconds=None,
    )

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert any(
        entry["path"] == str(fresh) and entry["reason"] == "missing_repo_path_field"
        for entry in report["stale_indexes"]
    )


# ---------------------------------------------------------------------------
# files_examined counts only index-shaped files
# ---------------------------------------------------------------------------


def test_prune_files_examined_counts_index_shaped_only(tmp_path):
    # Parity with prune_orphans.dirs_examined: only files matching the
    # canonical index_<16hex>.json shape are counted.
    missing_repo = tmp_path / "gone"
    _make_index(tmp_path, "real", repo_path=str(missing_repo))
    # Non-canonical siblings must be ignored.
    (tmp_path / "index_deadbeef.json").write_text("nope", encoding="utf-8")  # short hash
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
    (tmp_path / "index_not_hex_at_all0.json").write_text("nope", encoding="utf-8")

    report = prune_symbols(storage_dir=tmp_path, dry_run=True)

    assert report["files_examined"] == 1


# ---------------------------------------------------------------------------
# Edge cases for _classify JSON-shape tolerance
# (defensive paths unverified by the core AC tests; added at harden)
# ---------------------------------------------------------------------------


def test_classify_non_object_json_list(tmp_path):
    # Valid JSON but a list, not an object → unreadable_index_json.
    _make_index(tmp_path, "list-shape", raw_body="[1,2,3]")
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "unreadable_index_json" for e in results)


def test_classify_non_object_json_string(tmp_path):
    # Valid JSON but a bare string → unreadable_index_json.
    _make_index(tmp_path, "str-shape", raw_body='"just-a-string"')
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "unreadable_index_json" for e in results)


def test_classify_non_object_json_number(tmp_path):
    # Valid JSON but a bare number → unreadable_index_json.
    _make_index(tmp_path, "num-shape", raw_body="42")
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "unreadable_index_json" for e in results)


def test_classify_empty_file(tmp_path):
    # Zero-byte file → json.loads("") raises JSONDecodeError → unreadable_index_json.
    _make_index(tmp_path, "empty", raw_body="")
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "unreadable_index_json" for e in results)


def test_classify_repo_path_null(tmp_path):
    # Explicit {"repo_path": null} → missing_repo_path_field
    # (collapsed with missing-key and empty-string per parity with prune_orphans).
    _make_index(
        tmp_path,
        "null-path",
        raw_body='{"repo_path": null, "files": {}, "symbols": {}, "version": "2.0"}',
    )
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "missing_repo_path_field" for e in results)


def test_classify_repo_path_non_string(tmp_path):
    # Non-string repo_path (integer) → missing_repo_path_field.
    _make_index(
        tmp_path,
        "int-path",
        raw_body='{"repo_path": 123, "files": {}, "symbols": {}, "version": "2.0"}',
    )
    results = find_stale_indexes(storage_dir=tmp_path)
    assert any(e["reason"] == "missing_repo_path_field" for e in results)


# ---------------------------------------------------------------------------
# Structlog per-event emission (AC1 — observability parity)
# ---------------------------------------------------------------------------


def test_logging_refused_symlink_at_delete_time(tmp_path, monkeypatch):
    # execute branch symlink refusal emits log.warning("prune_refused_symlink").
    from structlog.testing import capture_logs

    from lgrep.tools import prune_symbols as module

    outside = tmp_path / "outside-target"
    outside.mkdir()
    storage = tmp_path / "symbols"
    storage.mkdir()
    link = storage / "index-feedfacefeedface.json"
    link.symlink_to(outside)

    def tampered(storage_dir, active_set=(), grace_seconds=None):
        return [
            {
                "path": str(link),
                "reason": "repo_path_enoent",
                "bytes": 1024,
                "repo_path": str(outside),
            }
        ]

    monkeypatch.setattr(module, "find_stale_indexes", tampered)

    with capture_logs() as cap:
        prune_symbols(storage_dir=storage, dry_run=False)

    matching = [e for e in cap if e["event"] == "prune_refused_symlink" and e["store"] == "symbols"]
    assert len(matching) == 1
    assert matching[0]["path"] == str(link)


def test_logging_refused_outside_root(tmp_path, monkeypatch):
    # execute branch path-confinement refusal emits log.warning("prune_refused_outside_root").
    from structlog.testing import capture_logs

    from lgrep.tools import prune_symbols as module

    escape_target = tmp_path / "escape-target"
    escape_target.mkdir()
    storage = tmp_path / "symbols"
    storage.mkdir()

    def tampered(storage_dir, active_set=(), grace_seconds=None):
        return [
            {
                "path": str(escape_target),
                "reason": "repo_path_enoent",
                "bytes": 1024,
                "repo_path": None,
            }
        ]

    monkeypatch.setattr(module, "find_stale_indexes", tampered)

    with capture_logs() as cap:
        prune_symbols(storage_dir=storage, dry_run=False)

    matching = [
        e for e in cap if e["event"] == "prune_refused_outside_root" and e["store"] == "symbols"
    ]
    assert len(matching) == 1


def test_logging_unlink_failed(tmp_path, monkeypatch):
    # execute branch unlink OSError emits log.warning("prune_unlink_failed").
    from structlog.testing import capture_logs

    missing_repo = tmp_path / "gone"
    stale = _make_index(tmp_path, "log-fail", repo_path=str(missing_repo))

    original_unlink = Path.unlink

    def flaky_unlink(self, *args, **kwargs):
        if self == stale:
            raise PermissionError("blocked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    with capture_logs() as cap:
        prune_symbols(storage_dir=tmp_path, dry_run=False)

    matching = [
        e
        for e in cap
        if e["event"] == "prune_unlink_failed"
        and e["store"] == "symbols"
        and e["path"] == str(stale)
    ]
    assert len(matching) == 1
    assert "blocked" in matching[0]["error"]


# ---------------------------------------------------------------------------
# Sidecar-cheap classification + sidecar/tmp reclamation (fixListReposStoreScan)
#
# Fixtures here are key-consistent: the index filename key is derived from
# repo_path exactly as IndexStore.save() derives it, unlike _make_index
# which keys by an arbitrary label.
# ---------------------------------------------------------------------------


def _store_key(repo_path: str) -> str:
    from lgrep.storage.index_store import _repo_key, normalize_repo_key

    return _repo_key(normalize_repo_key(repo_path))


def _write_index_with_sidecar(
    storage_root: Path,
    repo_path: str,
    *,
    index_body: str | None = None,
    corrupt_sidecar: bool = False,
    sidecar_repo_path: str | None = None,
    age_seconds: int | None = None,
) -> tuple[Path, Path]:
    """Write an index_{key}.json + index_{key}.meta.json pair keyed by repo_path."""
    key = _store_key(repo_path)
    index_file = storage_root / f"index_{key}.json"
    index_file.write_text(
        index_body
        if index_body is not None
        else json.dumps(
            {
                "repo_path": repo_path,
                "files": {},
                "symbols": {"s": {}},
                "occurrences": {},
                "version": "2.1",
            }
        ),
        encoding="utf-8",
    )
    sidecar = storage_root / f"index_{key}.meta.json"
    if corrupt_sidecar:
        sidecar.write_text("{ not json", encoding="utf-8")
    else:
        sidecar.write_text(
            json.dumps(
                {
                    "repo_path": sidecar_repo_path if sidecar_repo_path is not None else repo_path,
                    "version": "2.1",
                    "meta_version": 1,
                    "files": 0,
                    "symbols": 1,
                    "occurrences": 0,
                    "updated_at": _time.time(),
                }
            ),
            encoding="utf-8",
        )
    if age_seconds is not None:
        _age(index_file, age_seconds)
        _age(sidecar, age_seconds)
    return index_file, sidecar


class TestSidecarCheapClassification:
    """_classify must not parse the index body when a valid sidecar exists."""

    def test_valid_sidecar_skips_index_parse(self, tmp_path, monkeypatch):
        import json as json_mod

        from lgrep.tools import prune_symbols as module

        repo = tmp_path / "live-repo"
        repo.mkdir()
        index_file, _ = _write_index_with_sidecar(tmp_path, str(repo))

        # Make the index body unparseable-on-read: any full parse attempt
        # explodes, proving the sidecar path never touches the body.
        original_loads = json_mod.loads

        def guard_loads(raw, *args, **kwargs):
            if '"symbols"' in str(raw) and "meta_version" not in str(raw):
                raise AssertionError("_classify parsed an index body despite valid sidecar")
            return original_loads(raw, *args, **kwargs)

        monkeypatch.setattr(module.json, "loads", guard_loads)
        reason, repo_path = module._classify(index_file)
        assert reason is None  # live repo on disk
        assert repo_path == str(repo)

    def test_corrupt_index_with_valid_sidecar_is_trusted(self, tmp_path):
        """Documents the accepted blind spot: a key-verified sidecar is
        authoritative for repo_path even if the index body is corrupt.

        Post-fix writers cannot produce torn indexes (unique-temp +
        os.replace), so this state requires out-of-band corruption; the
        corrupt index self-heals on next re-index. Legacy corrupt indexes
        have no sidecars and still classify unreadable below.
        """
        from lgrep.tools.prune_symbols import _classify

        repo = tmp_path / "live-repo"
        repo.mkdir()
        index_file, _ = _write_index_with_sidecar(tmp_path, str(repo), index_body="{ torn garbage")

        reason, repo_path = _classify(index_file)
        assert reason is None
        assert repo_path == str(repo)

    def test_sidecarless_index_uses_parse_path(self, tmp_path):
        """Legacy indexes (no sidecar) keep EXACTLY today's classification."""
        from lgrep.tools.prune_symbols import _classify

        repo = tmp_path / "gone-repo"
        key = _store_key(str(repo))
        index_file = tmp_path / f"index_{key}.json"
        index_file.write_text(
            json.dumps(
                {
                    "repo_path": str(repo),
                    "files": {},
                    "symbols": {},
                    "occurrences": {},
                    "version": "2.0",
                }
            ),
            encoding="utf-8",
        )

        reason, repo_path = _classify(index_file)
        assert reason == "repo_path_enoent"
        assert repo_path == str(repo)

    def test_sidecarless_corrupt_index_still_unreadable(self, tmp_path):
        from lgrep.tools.prune_symbols import _classify

        index_file = tmp_path / f"index_{_key('torn')}.json"
        index_file.write_text("{ torn garbage", encoding="utf-8")

        reason, repo_path = _classify(index_file)
        assert reason == "unreadable_index_json"
        assert repo_path is None

    def test_foreign_key_sidecar_falls_back_to_parse(self, tmp_path):
        """A sidecar whose repo_path does not hash to the file key is distrusted."""
        from lgrep.tools.prune_symbols import _classify

        repo = tmp_path / "gone-repo"  # not created -> enoent via the INDEX body
        index_file, _ = _write_index_with_sidecar(
            tmp_path, str(repo), sidecar_repo_path="/some/other/repo"
        )

        reason, repo_path = _classify(index_file)
        assert reason == "repo_path_enoent"
        assert repo_path == str(repo)

    def test_corrupt_sidecar_falls_back_to_parse(self, tmp_path):
        from lgrep.tools.prune_symbols import _classify

        repo = tmp_path / "gone-repo"
        index_file, _ = _write_index_with_sidecar(tmp_path, str(repo), corrupt_sidecar=True)

        reason, repo_path = _classify(index_file)
        assert reason == "repo_path_enoent"
        assert repo_path == str(repo)

    def test_github_sidecar_skipped_like_parse_path(self, tmp_path):
        from lgrep.tools.prune_symbols import _classify

        repo = "github:owner/name@main"
        index_file, _ = _write_index_with_sidecar(tmp_path, repo)

        reason, repo_path = _classify(index_file)
        assert reason is None
        assert repo_path == repo


class TestSidecarAndTmpReclamation:
    """AC5: the prune path reclaims orphan sidecars and stale temp files,
    and removes a stale index's sidecar alongside it."""

    def test_orphan_sidecar_is_reclaimed(self, tmp_path):
        repo = str(tmp_path / "gone")
        _, sidecar = _write_index_with_sidecar(tmp_path, repo, age_seconds=7200)
        # Remove the index -> sidecar is orphaned.
        for p in tmp_path.glob("index_*.json"):
            if ".meta." not in p.name:
                p.unlink()

        monkeypatched = None  # grace via env set below
        import os as _os

        _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "3600"
        try:
            results = find_stale_indexes(storage_dir=tmp_path)
        finally:
            _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "0"

        assert any(
            entry["reason"] == "orphan_sidecar_json" and entry["path"] == str(sidecar)
            for entry in results
        ), results
        assert monkeypatched is None

    def test_orphan_sidecar_within_grace_is_preserved(self, tmp_path):
        import os as _os

        repo = str(tmp_path / "gone")
        _write_index_with_sidecar(tmp_path, repo)  # fresh mtimes
        for p in tmp_path.glob("index_*.json"):
            if ".meta." not in p.name:
                p.unlink()

        _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "3600"
        try:
            results = find_stale_indexes(storage_dir=tmp_path)
        finally:
            _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "0"

        assert not any(entry["reason"] == "orphan_sidecar_json" for entry in results), results

    def test_orphan_sidecar_of_active_repo_is_preserved(self, tmp_path):
        import os as _os

        repo_dir = tmp_path / "active-repo"
        repo_dir.mkdir()
        _, sidecar = _write_index_with_sidecar(tmp_path, str(repo_dir), age_seconds=7200)
        for p in tmp_path.glob("index_*.json"):
            if ".meta." not in p.name:
                p.unlink()

        _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "3600"
        try:
            results = find_stale_indexes(storage_dir=tmp_path, active_set=[str(repo_dir)])
        finally:
            _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "0"

        assert not any(entry["path"] == str(sidecar) for entry in results), results

    def test_stale_unique_temp_is_swept(self, tmp_path):
        tmp = tmp_path / f"index_{_key('x')}.12345.abcdef01.tmp"
        tmp.write_text("partial", encoding="utf-8")
        _age(tmp, 7200)

        results = find_stale_indexes(storage_dir=tmp_path)

        assert any(
            entry["reason"] == "stale_index_tmp" and entry["path"] == str(tmp) for entry in results
        ), results

    def test_stale_legacy_temp_is_swept(self, tmp_path):
        tmp = tmp_path / f"index_{_key('x')}.tmp"
        tmp.write_text("partial", encoding="utf-8")
        _age(tmp, 7200)

        results = find_stale_indexes(storage_dir=tmp_path)

        assert any(
            entry["reason"] == "stale_index_tmp" and entry["path"] == str(tmp) for entry in results
        ), results

    def test_fresh_temp_is_preserved(self, tmp_path):
        import os as _os

        tmp = tmp_path / f"index_{_key('x')}.12345.abcdef01.tmp"
        tmp.write_text("partial", encoding="utf-8")  # fresh mtime

        _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "3600"
        try:
            results = find_stale_indexes(storage_dir=tmp_path)
        finally:
            _os.environ["LGREP_PRUNE_MIN_AGE_S"] = "0"

        assert not any(entry["reason"] == "stale_index_tmp" for entry in results), results

    def test_lock_file_is_never_swept(self, tmp_path):
        lock = tmp_path / f".index_{_key('x')}.lock"
        lock.touch()
        _age(lock, 7200)

        results = find_stale_indexes(storage_dir=tmp_path)

        assert not any(".lock" in entry["path"] for entry in results), results

    def test_stale_index_removes_its_sidecar_too(self, tmp_path):
        """Execute must unlink the sidecar riding with a stale index (AC5)."""
        repo = str(tmp_path / "gone-repo")
        index_file, sidecar = _write_index_with_sidecar(tmp_path, repo, age_seconds=7200)

        report = prune_symbols(storage_dir=tmp_path, dry_run=False)

        assert not index_file.exists()
        assert not sidecar.exists(), "sidecar survived prune of its stale index"
        assert report["deleted_files"] == 2
        assert report["failures"] == []

    def test_dry_run_reports_sidecar_companion_bytes(self, tmp_path):
        repo = str(tmp_path / "gone-repo")
        index_file, sidecar = _write_index_with_sidecar(tmp_path, repo, age_seconds=7200)

        report = prune_symbols(storage_dir=tmp_path, dry_run=True)

        assert index_file.exists() and sidecar.exists(), "dry_run mutated the store"
        paths = [entry["path"] for entry in report["stale_indexes"]]
        assert str(index_file) in paths and str(sidecar) in paths, paths
        expected = index_file.stat().st_size + sidecar.stat().st_size
        assert report["reclaimed_bytes"] == expected

    def test_symlinked_sidecar_and_tmp_refused_at_scan(self, tmp_path):
        sidecar_target = tmp_path / "elsewhere-meta"
        sidecar_target.write_text("x", encoding="utf-8")
        tmp_target = tmp_path / "elsewhere-tmp"
        tmp_target.write_text("x", encoding="utf-8")
        _age(sidecar_target, 7200)
        _age(tmp_target, 7200)

        sidecar_link = tmp_path / f"index_{_key('y')}.meta.json"
        tmp_link = tmp_path / f"index_{_key('y')}.12345.abcdef01.tmp"
        sidecar_link.symlink_to(sidecar_target)
        tmp_link.symlink_to(tmp_target)

        results = find_stale_indexes(storage_dir=tmp_path)

        assert not any(entry["path"] == str(sidecar_link) for entry in results), results
        assert not any(entry["path"] == str(tmp_link) for entry in results), results

    def test_orphan_recheck_preserves_sidecar_of_recreated_index(self, tmp_path, monkeypatch):
        """Scan→unlink race: an orphan-classified sidecar whose index was
        recreated between scan and delete must NOT be unlinked."""
        from lgrep.tools import prune_symbols as module

        repo = tmp_path / "recreated-repo"
        repo.mkdir()
        index_file, sidecar = _write_index_with_sidecar(tmp_path, str(repo), age_seconds=7200)

        # Simulate a scan that ran while the index was missing.
        stale_entry = {
            "path": str(sidecar),
            "reason": "orphan_sidecar_json",
            "bytes": sidecar.stat().st_size,
            "repo_path": str(repo),
        }
        monkeypatch.setattr(module, "find_stale_indexes", lambda *a, **k: [stale_entry])

        report = module.prune_symbols(storage_dir=tmp_path, dry_run=False)

        assert sidecar.exists(), "sidecar of a recreated index was deleted"
        assert index_file.exists()
        assert report["deleted_files"] == 0
        assert report["failures"] == []

    def test_orphan_recheck_oserror_is_reported_without_deletion(self, tmp_path, monkeypatch):
        """An unreadable recreated-index check must not silently hide a failure."""
        from lgrep.tools import prune_symbols as module

        repo = tmp_path / "recheck-error-repo"
        repo.mkdir()
        _, sidecar = _write_index_with_sidecar(tmp_path, str(repo), age_seconds=7200)
        stale_entry = {
            "path": str(sidecar),
            "reason": "orphan_sidecar_json",
            "bytes": sidecar.stat().st_size,
            "repo_path": str(repo),
        }
        monkeypatch.setattr(module, "find_stale_indexes", lambda *a, **k: [stale_entry])
        monkeypatch.setattr(Path, "exists", lambda _self: (_ for _ in ()).throw(OSError("EIO")))

        report = module.prune_symbols(storage_dir=tmp_path, dry_run=False)
        monkeypatch.undo()

        assert sidecar.exists()
        assert report["deleted_files"] == 0
        assert report["failures"] == [{"path": str(sidecar), "error": "EIO"}]
