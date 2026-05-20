"""Tests for worktree-aware cache key resolution and lifecycle."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta
from lgrep.storage._chunk_store import canonical_repo_key


class TestCanonicalRepoKey:
    """Tests for canonical_repo_key resolution."""

    def test_canonical_key_non_git_path(self, tmp_path, monkeypatch):
        """Non-git path falls back to Path.resolve()."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        project = tmp_path / "myproject"
        project.mkdir()
        result = canonical_repo_key(project)
        assert result == project.resolve()

    def test_canonical_key_dedup_off(self, tmp_path, monkeypatch):
        """When LGREP_WORKTREE_DEDUP is unset, returns Path.resolve() even in git repo."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        # This project is a git repo, but dedup is off
        result = canonical_repo_key(Path.cwd())
        assert result == Path.cwd().resolve()

    def test_canonical_key_git_repo_returns_repo_root(self, monkeypatch):
        """Inside a git repo with dedup on, returns the repo root (parent of .git)."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        # This test runs inside a git worktree of the lgrep repo.
        # canonical_repo_key should resolve through the worktree to the
        # trunk repo root (where .git common-dir lives).
        cwd = Path.cwd().resolve()
        result = canonical_repo_key(cwd)
        # The result must be a directory containing .git
        assert (result / ".git").exists()
        # And it must be the same for any worktree of this repo
        assert result == canonical_repo_key(result)

    def test_canonical_key_git_worktree_returns_trunk(self, tmp_path, monkeypatch):
        """A git worktree resolves to the same key as its trunk repo."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")

        # Create a real git repo + worktree
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )

        worktree = tmp_path / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree)],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        try:
            trunk_key = canonical_repo_key(repo)
            worktree_key = canonical_repo_key(worktree)
            assert trunk_key == worktree_key, (
                f"Trunk key {trunk_key} != worktree key {worktree_key}"
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree), "--force"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

    def test_canonical_key_git_timeout_fallback(self, tmp_path, monkeypatch):
        """If git rev-parse times out, falls back to Path.resolve()."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        project = tmp_path / "project"
        project.mkdir()

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 2)):
            result = canonical_repo_key(project)
            assert result == project.resolve()


class TestDbPathDedup:
    """Tests for get_project_db_path with worktree dedup."""

    def test_two_worktrees_same_cache_dir(self, tmp_path, monkeypatch):
        """Two git worktrees produce the same cache dir when dedup is on."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")

        # Create a real git repo + worktree
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )

        worktree = tmp_path / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree)],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        try:
            db_trunk = get_project_db_path(repo)
            db_worktree = get_project_db_path(worktree)
            assert db_trunk == db_worktree, (
                f"Trunk cache {db_trunk} != worktree cache {db_worktree}"
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree), "--force"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

    def test_two_paths_different_cache_without_dedup(self, tmp_path, monkeypatch):
        """Two different paths produce different cache dirs when dedup is off."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        db1 = get_project_db_path(tmp_path / "a")
        db2 = get_project_db_path(tmp_path / "b")
        assert db1 != db2


class TestStaleFileDeletionGuard:
    """Tests for the stale-file deletion guard when dedup is enabled."""

    def test_stale_deletion_skipped_with_dedup(self, tmp_path, monkeypatch):
        """When dedup is on, stale files are NOT deleted from shared cache."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.indexing import Indexer
        from lgrep.storage import ChunkStore, get_project_db_path, EMBEDDING_DIM

        # Set up a project with one file
        project = tmp_path / "project"
        project.mkdir()
        (project / "real.py").write_text("print('hello')")

        # Create a store with a "stale" file chunk that doesn't exist on disk
        db_path = get_project_db_path(project)
        store = ChunkStore(db_path, project_path=project)

        # Manually insert a chunk for a file that doesn't exist
        stale_chunk = MagicMock()
        stale_chunk.file_path = "gone.py"
        stale_chunk.chunk_index = 0
        stale_chunk.start_line = 1
        stale_chunk.end_line = 5
        stale_chunk.text = "# stale content"
        stale_chunk.file_hash = "abc123"
        import hashlib
        import uuid
        from lgrep.storage import CodeChunk

        store.add_chunks(
            [
                CodeChunk(
                    id=str(uuid.uuid4()),
                    file_path="gone.py",
                    chunk_index=0,
                    start_line=1,
                    end_line=5,
                    content="# stale content",
                    vector=[0.1] * EMBEDDING_DIM,
                    file_hash="abc123",
                    indexed_at=1000.0,
                )
            ]
        )

        assert store.count_chunks() == 1

        # Create a mock embedder that returns zeros
        embedder = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.0] * EMBEDDING_DIM]
        embed_result.token_usage = 0
        embedder.embed_documents.return_value = embed_result

        indexer = Indexer(project, store, embedder)
        indexer.index_all()

        # The stale chunk should still exist because dedup is on
        indexed_files = store.get_indexed_files()
        assert "gone.py" in indexed_files, (
            "Stale file was deleted despite dedup being on — "
            "this would corrupt shared cache across worktrees"
        )

    def test_stale_deletion_runs_without_dedup(self, tmp_path, monkeypatch):
        """When dedup is off, stale files ARE deleted (existing behavior)."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.indexing import Indexer
        from lgrep.storage import ChunkStore, get_project_db_path, EMBEDDING_DIM, CodeChunk
        import uuid

        project = tmp_path / "project"
        project.mkdir()
        (project / "real.py").write_text("print('hello')")

        db_path = get_project_db_path(project)
        store = ChunkStore(db_path, project_path=project)

        # Insert stale chunk
        store.add_chunks(
            [
                CodeChunk(
                    id=str(uuid.uuid4()),
                    file_path="gone.py",
                    chunk_index=0,
                    start_line=1,
                    end_line=5,
                    content="# stale",
                    vector=[0.1] * EMBEDDING_DIM,
                    file_hash="abc123",
                    indexed_at=1000.0,
                )
            ]
        )

        embedder = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.0] * EMBEDDING_DIM]
        embed_result.token_usage = 0
        embedder.embed_documents.return_value = embed_result

        indexer = Indexer(project, store, embedder)
        indexer.index_all()

        indexed_files = store.get_indexed_files()
        assert "gone.py" not in indexed_files, (
            "Stale file was NOT deleted when dedup is off — existing behavior should be preserved"
        )


class TestAliasPaths:
    """Tests for alias_paths support in project_meta.json."""

    def test_write_meta_with_aliases(self, tmp_path, monkeypatch):
        """write_project_meta includes alias_paths field."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)

        write_project_meta(
            project,
            db_path=db_path,
            alias_paths=["/worktree/a", "/worktree/b"],
        )

        meta = read_project_meta(db_path)
        assert meta is not None
        assert "alias_paths" in meta
        assert "/worktree/a" in meta["alias_paths"]
        assert "/worktree/b" in meta["alias_paths"]
        assert meta["project_path"] == str(project.resolve())

    def test_write_meta_appends_aliases(self, tmp_path, monkeypatch):
        """Writing with a new alias preserves existing aliases."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)

        # First write with one alias
        write_project_meta(project, db_path=db_path, alias_paths=["/worktree/a"])

        # Second write should append, not replace
        write_project_meta(project, db_path=db_path, alias_paths=["/worktree/b"])

        meta = read_project_meta(db_path)
        assert meta is not None
        assert "/worktree/a" in meta["alias_paths"]
        assert "/worktree/b" in meta["alias_paths"]

    def test_write_meta_no_aliases_omits_field(self, tmp_path, monkeypatch):
        """When no aliases, alias_paths field is absent or empty."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)

        write_project_meta(project, db_path=db_path)

        meta = read_project_meta(db_path)
        assert meta is not None
        assert meta.get("alias_paths", []) == []


class TestStartupOrphanSweep:
    """Tests for background orphan sweep on server start."""

    def test_startup_sweep_called(self, tmp_path, monkeypatch):
        """_schedule_startup_sweep calls prune_orphans with active projects."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        import asyncio
        from lgrep.server.lifecycle import _schedule_startup_sweep, LgrepContext
        from unittest.mock import patch, MagicMock

        ctx = LgrepContext()
        ctx.projects = {
            "/active/project": MagicMock(),
        }

        captured_active = None

        async def fake_sleep(seconds):
            pass  # Skip the 5-minute delay

        def mock_prune(dry_run, active_set, **kwargs):
            nonlocal captured_active
            captured_active = active_set
            return {
                "dry_run": dry_run,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }

        with patch.object(asyncio, "sleep", side_effect=fake_sleep):
            with patch("lgrep.tools.prune_orphans.prune_orphans", side_effect=mock_prune):
                asyncio.run(_schedule_startup_sweep(ctx))

        assert captured_active is not None
        assert "/active/project" in captured_active

    def test_startup_sweep_cancels_on_shutdown(self):
        """Sweep task is cancelled when server shuts down before 5-min delay."""
        import asyncio
        from lgrep.server.lifecycle import _schedule_startup_sweep, LgrepContext
        from unittest.mock import patch

        ctx = LgrepContext()
        sweep_ran = False

        def mock_prune(*args, **kwargs):
            nonlocal sweep_ran
            sweep_ran = True
            return {
                "dry_run": False,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }

        async def run_and_cancel():
            # Real sleep that we can cancel
            task = asyncio.create_task(_schedule_startup_sweep(ctx))
            await asyncio.sleep(0.01)  # Let it start sleeping
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return sweep_ran

        with patch("lgrep.tools.prune_orphans.prune_orphans", side_effect=mock_prune):
            result = asyncio.run(run_and_cancel())

        assert not result, "Sweep should NOT have run prune_orphans after cancellation"
