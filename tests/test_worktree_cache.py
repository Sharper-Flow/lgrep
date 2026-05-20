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


class TestWorktreeDedupE2E:
    """End-to-end integration test for worktree dedup."""

    def test_two_worktrees_one_cache_dir(self, tmp_path, monkeypatch):
        """Two git worktrees of the same repo produce one cache dir with dedup."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        # Create a real git repo + worktree
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "hello.py").write_text("print('hello')")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
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
            from lgrep.storage import get_project_db_path

            db_trunk = get_project_db_path(repo)
            db_worktree = get_project_db_path(worktree)

            # AC#1: Same cache dir
            assert db_trunk == db_worktree, f"Cache dirs differ: {db_trunk} vs {db_worktree}"

            # AC#10: Non-git paths still produce different caches (no regression)
            random_path = tmp_path / "random"
            random_path.mkdir()
            db_random = get_project_db_path(random_path)
            assert db_random != db_trunk
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree), "--force"],
                cwd=repo,
                check=True,
                capture_output=True,
            )


class TestInvalidateWorktreeCache:
    """Tests for invalidate_worktree_cache tool implementation."""

    def _make_git_repo(self, tmp_path):
        """Helper: create a git repo with one empty commit."""
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
        return repo

    def test_invalidate_removes_alias(self, tmp_path, monkeypatch):
        """Invalidation removes the worktree alias from meta, keeps canonical."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")

        from lgrep.tools.invalidate_worktree import invalidate_worktree_cache

        repo = self._make_git_repo(tmp_path)
        worktree = tmp_path / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(worktree)],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        try:
            db_path = get_project_db_path(repo)
            write_project_meta(
                repo,
                db_path=db_path,
                alias_paths=[str(worktree.resolve())],
            )

            # Verify alias is there
            meta = read_project_meta(db_path)
            assert meta is not None
            assert str(worktree.resolve()) in meta.get("alias_paths", [])

            # Create placeholder chunks.lance
            (db_path / "chunks.lance").mkdir(parents=True, exist_ok=True)

            # Invalidate the worktree (resolves to same cache dir via dedup)
            entries, paths_cleaned, bytes_reclaimed = invalidate_worktree_cache(
                paths=[str(worktree)],
                cache_dir=tmp_path / "cache",
            )

            assert paths_cleaned == 1
            assert len(entries) == 1
            entry = entries[0]
            assert entry["alias_removed"] is True
            assert entry["cache_deleted"] is False
            assert entry["error"] is None

            # Verify alias is gone from meta
            meta_after = read_project_meta(db_path)
            assert meta_after is not None
            assert str(worktree.resolve()) not in meta_after.get("alias_paths", [])
            # Canonical project_path still in meta
            assert meta_after["project_path"] == str(repo.resolve())

            # Cache dir still exists (canonical project still there)
            assert db_path.is_dir()
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(worktree), "--force"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

    def test_invalidate_deletes_orphan_cache(self, tmp_path, monkeypatch):
        """When canonical is gone and no aliases remain, cache dir is deleted."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.tools.invalidate_worktree import invalidate_worktree_cache

        # Create a project, write meta with the project as canonical
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path)

        # Create a placeholder chunks.lance
        (db_path / "chunks.lance").mkdir(parents=True, exist_ok=True)

        # Now delete the canonical project dir
        import shutil

        shutil.rmtree(project)
        assert not project.exists()

        # Invalidate the project path — canonical is gone, no aliases
        entries, paths_cleaned, bytes_reclaimed = invalidate_worktree_cache(
            paths=[str(project)],
            cache_dir=tmp_path / "cache",
        )

        assert paths_cleaned == 1
        entry = entries[0]
        assert entry["cache_deleted"] is True
        assert bytes_reclaimed > 0

        # Cache dir should be gone
        assert not db_path.exists()

    def test_invalidate_refuses_outside_cache(self, tmp_path, monkeypatch):
        """Path with no cache dir at all returns error."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.tools.invalidate_worktree import invalidate_worktree_cache

        nowhere = tmp_path / "nonexistent" / "deep" / "path"
        entries, paths_cleaned, bytes_reclaimed = invalidate_worktree_cache(
            paths=[str(nowhere)],
            cache_dir=tmp_path / "cache",
        )

        # No cache dir exists → entry with error
        assert paths_cleaned == 0
        assert len(entries) == 1
        assert entries[0]["error"] is not None

    def test_invalidate_refuses_symlink(self, tmp_path, monkeypatch):
        """Symlinked path returns error."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.tools.invalidate_worktree import invalidate_worktree_cache

        project = tmp_path / "project"
        project.mkdir()
        symlink = tmp_path / "symlink_to_project"
        symlink.symlink_to(project)

        # Create cache for the real project
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path)
        (db_path / "chunks.lance").mkdir(parents=True, exist_ok=True)

        entries, paths_cleaned, bytes_reclaimed = invalidate_worktree_cache(
            paths=[str(symlink)],
            cache_dir=tmp_path / "cache",
        )

        assert paths_cleaned == 0
        assert len(entries) == 1
        assert entries[0]["error"] is not None
        assert "symlink" in entries[0]["error"].lower()

    def test_invalidate_batch(self, tmp_path, monkeypatch):
        """Multiple paths processed in one call."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")

        from lgrep.tools.invalidate_worktree import invalidate_worktree_cache

        repo = self._make_git_repo(tmp_path)
        wt1 = tmp_path / "worktree1"
        wt2 = tmp_path / "worktree2"
        subprocess.run(
            ["git", "worktree", "add", str(wt1)],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "worktree", "add", str(wt2)],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        try:
            db_path = get_project_db_path(repo)
            write_project_meta(
                repo,
                db_path=db_path,
                alias_paths=[str(wt1.resolve()), str(wt2.resolve())],
            )
            (db_path / "chunks.lance").mkdir(parents=True, exist_ok=True)

            entries, paths_cleaned, bytes_reclaimed = invalidate_worktree_cache(
                paths=[str(wt1), str(wt2)],
                cache_dir=tmp_path / "cache",
            )

            assert paths_cleaned == 2
            assert len(entries) == 2
            for entry in entries:
                assert entry["alias_removed"] is True
                assert entry["error"] is None

            # Both aliases gone
            meta = read_project_meta(db_path)
            assert meta is not None
            aliases = meta.get("alias_paths", [])
            assert str(wt1.resolve()) not in aliases
            assert str(wt2.resolve()) not in aliases
        finally:
            for wt in (wt1, wt2):
                subprocess.run(
                    ["git", "worktree", "remove", str(wt), "--force"],
                    cwd=repo,
                    check=False,
                    capture_output=True,
                )


class TestInMemoryDedup:
    """Tests for shared ProjectState across worktree paths."""

    def _make_repo_with_worktree(self, tmp_path):
        """Helper: create a git repo + worktree, return (repo, worktree)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "hello.py").write_text("print('hello')")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
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
        return repo, worktree

    def _cleanup_worktree(self, repo, worktree):
        subprocess.run(
            ["git", "worktree", "remove", str(worktree), "--force"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    def test_inmemory_dedup_shares_state(self, tmp_path, monkeypatch):
        """With dedup on, trunk and worktree get the SAME ProjectState object."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

        import asyncio
        from lgrep.server.lifecycle import LgrepContext, _ensure_project_initialized

        repo, worktree = self._make_repo_with_worktree(tmp_path)
        try:
            ctx = LgrepContext(voyage_api_key="fake-key-for-test")
            with patch("lgrep.server.lifecycle.VoyageEmbedder") as mock_embedder:
                mock_embedder.return_value = MagicMock()

                state_trunk = asyncio.run(_ensure_project_initialized(ctx, repo))
                state_worktree = asyncio.run(_ensure_project_initialized(ctx, worktree))

            # Both must be ProjectState (not error dicts)
            from lgrep.server.lifecycle import ProjectState

            assert isinstance(state_trunk, ProjectState)
            assert isinstance(state_worktree, ProjectState)
            # AC: same Python object — memory is shared
            assert state_trunk is state_worktree, (
                "Trunk and worktree ProjectStates should be the same object when dedup is enabled"
            )
        finally:
            self._cleanup_worktree(repo, worktree)

    def test_inmemory_dedup_disabled_separate_state(self, tmp_path, monkeypatch):
        """With dedup off, trunk and worktree get DIFFERENT ProjectState objects (no regression)."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

        import asyncio
        from lgrep.server.lifecycle import LgrepContext, _ensure_project_initialized, ProjectState

        repo, worktree = self._make_repo_with_worktree(tmp_path)
        try:
            ctx = LgrepContext(voyage_api_key="fake-key-for-test")
            with patch("lgrep.server.lifecycle.VoyageEmbedder") as mock_embedder:
                mock_embedder.return_value = MagicMock()

                state_trunk = asyncio.run(_ensure_project_initialized(ctx, repo))
                state_worktree = asyncio.run(_ensure_project_initialized(ctx, worktree))

            assert isinstance(state_trunk, ProjectState)
            assert isinstance(state_worktree, ProjectState)
            assert state_trunk is not state_worktree, (
                "With dedup OFF, states should be separate objects"
            )
        finally:
            self._cleanup_worktree(repo, worktree)

    def test_inmemory_dedup_removal_safe(self, tmp_path, monkeypatch):
        """Removing one aliased path doesn't tear down state shared by another."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

        import asyncio
        from lgrep.server import remove_project
        from lgrep.server.lifecycle import LgrepContext, _ensure_project_initialized

        repo, worktree = self._make_repo_with_worktree(tmp_path)
        try:
            ctx = LgrepContext(voyage_api_key="fake-key-for-test")
            with patch("lgrep.server.lifecycle.VoyageEmbedder") as mock_embedder:
                mock_embedder.return_value = MagicMock()

                state_trunk = asyncio.run(_ensure_project_initialized(ctx, repo))
                state_worktree = asyncio.run(_ensure_project_initialized(ctx, worktree))

            # Remove worktree path
            result = remove_project(ctx, str(worktree))
            assert result["removed"] is True

            # Trunk path should still work
            trunk_key = str(repo.resolve())
            assert trunk_key in ctx.projects, (
                "Trunk should remain accessible after removing aliased worktree path"
            )
        finally:
            self._cleanup_worktree(repo, worktree)
