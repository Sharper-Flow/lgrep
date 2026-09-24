"""Tests for worktree-aware cache key resolution and lifecycle."""

import contextlib
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

    def test_stale_deletion_with_dedup_keeps_overlay_rows(self, tmp_path, monkeypatch):
        """With dedup on, base stale deletion removes only base rows."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        import uuid

        from lgrep.embeddings import MODEL_NAME
        from lgrep.indexing import Indexer
        from lgrep.storage import EMBEDDING_DIM, ChunkStore, CodeChunk, get_project_db_path

        project = tmp_path / "project"
        project.mkdir()
        (project / "real.py").write_text("print('hello')")

        store = ChunkStore(get_project_db_path(project), project_path=project)
        overlay = store.for_checkout(str(tmp_path / "worktree"))

        def _chunk(content: str) -> CodeChunk:
            return CodeChunk(
                id=str(uuid.uuid4()),
                file_path="gone.py",
                chunk_index=0,
                start_line=1,
                end_line=5,
                content=content,
                vector=[0.1] * EMBEDDING_DIM,
                file_hash="abc123",
                indexed_at=1000.0,
                embedding_model=MODEL_NAME,
            )

        store.add_chunks([_chunk("# stale base")])
        overlay.add_chunks([_chunk("# worktree copy")])

        embedder = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.0] * EMBEDDING_DIM]
        embed_result.token_usage = 0
        embed_result.model = "voyage-code-4"
        embedder.embed_documents.return_value = embed_result

        Indexer(project, store, embedder).index_all()

        assert "gone.py" not in store.get_indexed_files()
        assert overlay.get_file_hash("gone.py") == "abc123"

    def test_stale_deletion_runs_without_dedup(self, tmp_path, monkeypatch):
        """When dedup is off, stale files ARE deleted (existing behavior)."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        import uuid

        from lgrep.embeddings import MODEL_NAME
        from lgrep.indexing import Indexer
        from lgrep.storage import EMBEDDING_DIM, ChunkStore, CodeChunk, get_project_db_path

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
                    embedding_model=MODEL_NAME,
                )
            ]
        )

        embedder = MagicMock()
        embed_result = MagicMock()
        embed_result.embeddings = [[0.0] * EMBEDDING_DIM]
        embed_result.token_usage = 0
        embed_result.model = "voyage-code-4"
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
        from unittest.mock import MagicMock, patch

        from lgrep.server.lifecycle import LgrepContext, _schedule_startup_sweep

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

        with (
            patch.object(asyncio, "sleep", side_effect=fake_sleep),
            patch("lgrep.tools.prune_orphans.prune_orphans", side_effect=mock_prune),
        ):
            asyncio.run(_schedule_startup_sweep(ctx))

        assert captured_active is not None
        assert "/active/project" in captured_active

    def test_startup_sweep_cancels_on_shutdown(self):
        """Sweep task is cancelled when server shuts down before 5-min delay."""
        import asyncio
        from unittest.mock import patch

        from lgrep.server.lifecycle import LgrepContext, _schedule_startup_sweep

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
            with contextlib.suppress(asyncio.CancelledError):
                await task
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

    def test_inmemory_dedup_shares_store_not_state(self, tmp_path, monkeypatch):
        """With dedup on, each checkout has its own state over one shared store."""
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

        import asyncio

        from lgrep.server.lifecycle import LgrepContext, ProjectState, _ensure_project_initialized
        from lgrep.storage import BASE_CHECKOUT, OverlayStore

        repo, worktree = self._make_repo_with_worktree(tmp_path)
        try:
            ctx = LgrepContext(voyage_api_key="fake-key-for-test")
            with patch("lgrep.server.lifecycle.VoyageEmbedder") as mock_embedder:
                mock_embedder.return_value = MagicMock()

                state_trunk = asyncio.run(_ensure_project_initialized(ctx, repo))
                state_worktree = asyncio.run(_ensure_project_initialized(ctx, worktree))

            assert isinstance(state_trunk, ProjectState)
            assert isinstance(state_worktree, ProjectState)
            assert state_trunk is not state_worktree
            assert len(ctx._stores) == 1
            assert isinstance(state_worktree.db, OverlayStore)
            assert state_worktree.db.for_checkout(BASE_CHECKOUT) is state_trunk.db
            assert state_worktree.indexer.project_path == worktree.resolve()
            assert state_worktree.base_path == str(repo.resolve())
            meta = read_project_meta(state_trunk.db.db_path)
            assert meta["project_path"] == str(repo.resolve())
            assert str(worktree.resolve()) in meta["alias_paths"]
        finally:
            self._cleanup_worktree(repo, worktree)

    def test_inmemory_dedup_disabled_separate_state(self, tmp_path, monkeypatch):
        """With dedup off, trunk and worktree get DIFFERENT ProjectState objects (no regression)."""
        monkeypatch.delenv("LGREP_WORKTREE_DEDUP", raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-for-test")

        import asyncio

        from lgrep.server.lifecycle import LgrepContext, ProjectState, _ensure_project_initialized

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

                asyncio.run(_ensure_project_initialized(ctx, repo))
                asyncio.run(_ensure_project_initialized(ctx, worktree))

            # Remove worktree path
            result = remove_project(ctx, str(worktree))
            assert result["removed"] is True

            # Trunk path and its store should still work
            trunk_key = str(repo.resolve())
            assert trunk_key in ctx.projects, (
                "Trunk should remain accessible after removing its worktree path"
            )
            assert ctx.projects[trunk_key].db in ctx._stores.values()
        finally:
            self._cleanup_worktree(repo, worktree)


class TestGcWorktreeMeta:
    """Tests for gc_worktree_meta — stale alias cleanup."""

    def test_gc_removes_stale_aliases(self, tmp_path, monkeypatch):
        """gc_worktree_meta removes alias entries whose paths no longer exist."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta
        from lgrep.tools.prune_orphans import gc_worktree_meta

        project = tmp_path / "project"
        project.mkdir()
        live_alias = tmp_path / "live_worktree"
        live_alias.mkdir()
        dead_alias = tmp_path / "dead_worktree"  # never created
        db_path = get_project_db_path(project)

        write_project_meta(
            project,
            db_path=db_path,
            alias_paths=[str(live_alias), str(dead_alias)],
        )

        report = gc_worktree_meta(dry_run=False)

        meta = read_project_meta(db_path)
        assert meta is not None
        aliases = meta.get("alias_paths", [])
        assert str(live_alias) in aliases, "Live alias must be preserved"
        assert str(dead_alias) not in aliases, "Dead alias must be removed"
        assert report["aliases_removed"] >= 1
        assert report["dirs_updated"] >= 1

    def test_gc_keeps_all_when_all_live(self, tmp_path, monkeypatch):
        """When all aliases point to live dirs, none are removed."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta
        from lgrep.tools.prune_orphans import gc_worktree_meta

        project = tmp_path / "project"
        project.mkdir()
        a = tmp_path / "a"
        a.mkdir()
        b = tmp_path / "b"
        b.mkdir()
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path, alias_paths=[str(a), str(b)])

        report = gc_worktree_meta(dry_run=False)

        meta = read_project_meta(db_path)
        assert meta is not None
        aliases = meta.get("alias_paths", [])
        assert str(a) in aliases
        assert str(b) in aliases
        assert report["aliases_removed"] == 0

    def test_gc_dry_run_no_writes(self, tmp_path, monkeypatch):
        """Dry-run reports what would be removed but doesn't change meta."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta
        from lgrep.tools.prune_orphans import gc_worktree_meta

        project = tmp_path / "project"
        project.mkdir()
        dead = tmp_path / "dead"
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path, alias_paths=[str(dead)])

        report = gc_worktree_meta(dry_run=True)

        # Dry run reports the find
        assert report["aliases_removed"] >= 1
        # But meta is unchanged
        meta = read_project_meta(db_path)
        assert meta is not None
        assert str(dead) in meta.get("alias_paths", [])

    def test_gc_handles_no_meta(self, tmp_path, monkeypatch):
        """Cache dirs without project_meta.json are gracefully skipped."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        # Create a fake cache dir that looks like a semantic cache but has no meta
        cache_root = tmp_path / "cache"
        cache_root.mkdir()
        fake_dir = cache_root / "abc123def456"  # 12-hex-char name (matches shape filter)
        fake_dir.mkdir()
        (fake_dir / "chunks.lance").mkdir()

        from lgrep.tools.prune_orphans import gc_worktree_meta

        report = gc_worktree_meta(dry_run=False)
        # No error, no work done on dirs without meta
        assert report["checked"] >= 1

    def test_lgrep_gc_runs_both_passes(self, tmp_path, monkeypatch, capsys):
        """lgrep gc --execute runs both prune_orphans AND gc_worktree_meta."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.cli import _cmd_gc

        with (
            patch("lgrep.tools.prune_orphans.prune_orphans") as mock_prune,
            patch("lgrep.tools.prune_orphans.gc_worktree_meta") as mock_gc,
        ):
            mock_prune.return_value = {
                "dry_run": False,
                "dirs_examined": 0,
                "orphans": [],
                "skipped_active": [],
                "deleted_dirs": 0,
                "reclaimed_bytes": 0,
                "failures": [],
            }
            mock_gc.return_value = {
                "dry_run": False,
                "checked": 0,
                "aliases_removed": 0,
                "dirs_updated": 0,
            }
            rc = _cmd_gc(["--execute"])

        assert rc == 0
        mock_prune.assert_called_once()
        mock_gc.assert_called_once()


class TestAliasFlock:
    """Tests for fcntl.flock guard on write_project_meta read-modify-write."""

    def test_concurrent_alias_writes_no_data_loss(self, tmp_path, monkeypatch):
        """Two concurrent processes writing different aliases — both land in final meta."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from multiprocessing import Process

        from lgrep.storage import get_project_db_path, read_project_meta

        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)
        db_path.mkdir(parents=True, exist_ok=True)

        def writer(project_str: str, db_path_str: str, alias: str, cache_dir: str):
            import os

            os.environ["LGREP_CACHE_DIR"] = cache_dir
            from pathlib import Path

            from lgrep.storage import write_project_meta

            for _ in range(20):  # Multiple writes to maximize race window
                write_project_meta(
                    project_str,
                    db_path=Path(db_path_str),
                    alias_paths=[alias],
                )

        procs = []
        for i in range(4):
            p = Process(
                target=writer,
                args=(
                    str(project),
                    str(db_path),
                    f"/worktree/alias_{i}",
                    str(tmp_path / "cache"),
                ),
            )
            procs.append(p)
            p.start()

        for p in procs:
            p.join(timeout=10)
            assert p.exitcode == 0, f"Writer process failed: exitcode={p.exitcode}"

        meta = read_project_meta(db_path)
        assert meta is not None
        aliases = set(meta.get("alias_paths", []))
        # All 4 aliases must be present — flock prevents lost updates
        for i in range(4):
            assert f"/worktree/alias_{i}" in aliases, (
                f"Lost alias /worktree/alias_{i} due to concurrent-write race (got {aliases})"
            )

    def test_flock_called_during_rmw(self, tmp_path, monkeypatch):
        """fcntl.flock is called with LOCK_EX before read, LOCK_UN after rename."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.storage import get_project_db_path, write_project_meta

        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)

        with patch("fcntl.flock") as mock_flock:
            write_project_meta(project, db_path=db_path, alias_paths=["/wt/a"])

        # Must have been called at least twice: LOCK_EX then LOCK_UN
        assert mock_flock.call_count >= 2
        # First call should request LOCK_EX (some integer >= 2)
        import fcntl as _fcntl

        first_call_op = mock_flock.call_args_list[0][0][1]
        assert first_call_op == _fcntl.LOCK_EX
        last_call_op = mock_flock.call_args_list[-1][0][1]
        assert last_call_op == _fcntl.LOCK_UN

    def test_flock_unavailable_graceful(self, tmp_path, monkeypatch):
        """When fcntl is unavailable (Windows), write still succeeds with warning."""
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))

        from lgrep.storage import get_project_db_path, read_project_meta, write_project_meta

        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)

        # Patch the import inside write_project_meta to raise ImportError
        import sys

        original_fcntl = sys.modules.get("fcntl")
        sys.modules["fcntl"] = None  # type: ignore[assignment]
        try:
            # Force re-import path by patching the lookup
            with patch.dict(sys.modules, {"fcntl": None}):
                write_project_meta(project, db_path=db_path, alias_paths=["/wt/no_lock"])
        finally:
            if original_fcntl is not None:
                sys.modules["fcntl"] = original_fcntl

        meta = read_project_meta(db_path)
        assert meta is not None
        assert "/wt/no_lock" in meta.get("alias_paths", [])


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}

_TRUNK_SHARED = "def shared():\n    return 'trunk version'\n"
_BRANCH_SHARED = "def shared():\n    return 'branch version'\n"


class _RecordingEmbedder:
    """Embedder double: deterministic vectors, records every embedded text."""

    def __init__(self):
        self.texts: list[str] = []

    def embed_documents(self, texts, cancel_event=None):
        from types import SimpleNamespace

        from lgrep.embeddings import MODEL_NAME

        self.texts.extend(texts)
        return SimpleNamespace(
            embeddings=[_vector(t) for t in texts],
            token_usage=len(texts),
            model=MODEL_NAME,
        )


def _vector(text: str) -> list[float]:
    import hashlib

    from lgrep.storage import EMBEDDING_DIM

    digest = hashlib.sha256(text.encode()).digest()
    return [(digest[i % 32] - 128) / 128.0 for i in range(EMBEDDING_DIM)]


class TestWorktreeOverlay:
    """Base + overlay semantic cache under LGREP_WORKTREE_DEDUP.

    Trunk files: shared.py, same.py, trunk_only.py. The worktree changes
    shared.py, keeps same.py, deletes trunk_only.py, and adds branch_only.py.
    """

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LGREP_WORKTREE_DEDUP", "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "shared.py").write_text(_TRUNK_SHARED)
        (repo / "same.py").write_text("def same():\n    return 'unchanged'\n")
        (repo / "trunk_only.py").write_text("def trunk_only():\n    return 'trunk only'\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=_GIT_ENV)
        worktree = tmp_path / "wt"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)], cwd=repo, check=True
        )
        (worktree / "shared.py").write_text(_BRANCH_SHARED)
        (worktree / "trunk_only.py").unlink()
        (worktree / "branch_only.py").write_text("def branch_only():\n    return 'branch only'\n")
        return repo.resolve(), worktree.resolve()

    def _index(self, path, embedder):
        """Index a checkout through a view of one shared owner store, as the server does."""
        from lgrep.indexing import Indexer
        from lgrep.storage import ChunkStore, checkout_scope, get_project_db_path

        owner, checkout = checkout_scope(path)
        owners = self.__dict__.setdefault("_owners", {})
        if owner not in owners:
            owners[owner] = ChunkStore(get_project_db_path(owner), project_path=owner)
        store = owners[owner].for_checkout(checkout)
        Indexer(path, store, embedder).index_all()
        return store

    def _visible(self, store) -> dict[str, str]:
        """Map each file the store's vector and hybrid searches return to its text."""
        hits: dict[str, list[str]] = {}
        vector = store.search_vector(_vector("probe"), limit=100).results
        hybrid = store.search_hybrid(_vector("probe"), "return", limit=100).results
        for result in [*vector, *hybrid]:
            hits.setdefault(result.file_path, []).append(result.content)
        return {path: "\n".join(sorted(set(texts))) for path, texts in hits.items()}

    def test_worktree_sees_own_files(self, tmp_path, monkeypatch):
        """A worktree searches its own version of every file, one query per search."""
        import asyncio

        from lgrep.server.lifecycle import LgrepContext, _ensure_project_initialized

        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)
        store = self._index(worktree, embedder)

        visible = self._visible(store)
        assert set(visible) == {"shared.py", "same.py", "branch_only.py"}
        assert "branch version" in visible["shared.py"]
        assert "trunk version" not in visible["shared.py"]
        assert store.get_indexed_files() == {"shared.py", "same.py", "branch_only.py"}

        ctx = LgrepContext(voyage_api_key="k")
        with patch("lgrep.server.lifecycle.VoyageEmbedder", return_value=MagicMock()):
            state = asyncio.run(_ensure_project_initialized(ctx, worktree))
        assert state.indexer.project_path == worktree
        found = {str(p.relative_to(worktree)) for p in state.indexer.discovery.find_files()}
        assert found == {"shared.py", "same.py", "branch_only.py"}

    @pytest.mark.parametrize("scenario", ["fresh", "overlay_indexed_before", "all_files_differ"])
    def test_first_worktree_search_never_serves_trunk_versions(
        self, tmp_path, monkeypatch, scenario
    ):
        """A worktree's first search, before its overlay is embedded, hides the
        base rows of files it changed or deleted and schedules the embedding."""
        import asyncio
        from unittest.mock import AsyncMock

        from mcp.server.fastmcp import Context

        from lgrep.server import search_semantic
        from lgrep.server.lifecycle import LgrepContext

        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)
        # A branch file edited after an earlier overlay pass must not serve
        # its stale overlay rows either.
        if scenario == "overlay_indexed_before":
            (worktree / "branch_only.py").write_text("def branch_only():\n    return 'old'\n")
            self._index(worktree, embedder)
            (worktree / "branch_only.py").write_text(
                "def branch_only():\n    return 'branch only'\n"
            )
        (repo / "same.py").write_text("def same():\n    return 'unchanged'\n# trunk moved\n")
        (worktree / "same.py").write_text("def same():\n    return 'unchanged'\n# trunk moved\n")
        if scenario == "all_files_differ":
            (worktree / "same.py").write_text("def same():\n    return 'branch same'\n")
        self._index(repo, embedder)

        app_ctx = LgrepContext(voyage_api_key="k")
        query_embedder = MagicMock()
        query_embedder.embed_query_async = AsyncMock(return_value=_vector("probe"))
        ctx = MagicMock(spec=Context)
        ctx.request_context.lifespan_context = app_ctx
        with (
            patch("lgrep.server.lifecycle.VoyageEmbedder", return_value=query_embedder),
            patch(
                "lgrep.server.tools_semantic._schedule_background_reindex", new=AsyncMock()
            ) as schedule,
        ):
            response = asyncio.run(
                search_semantic(query="return", path=str(worktree), limit=50, ctx=ctx)
            )

        assert "results" in response, response
        hits = {hit["file_path"]: hit["snippet"] for hit in response["results"]}
        assert "trunk_only.py" not in hits
        assert "trunk version" not in hits.get("shared.py", "")
        assert "old" not in hits.get("branch_only.py", "")
        if scenario == "all_files_differ":
            assert hits == {}
        else:
            assert "same.py" in hits
        assert schedule.await_count == 1

    def test_first_cli_worktree_search_never_serves_trunk_versions(
        self, tmp_path, monkeypatch, capsys
    ):
        """The CLI search reconciles a worktree overlay before searching it."""
        import json

        from lgrep.cli import _cmd_search_semantic

        repo, worktree = self._setup(tmp_path, monkeypatch)
        self._index(repo, _RecordingEmbedder())
        monkeypatch.setenv("VOYAGE_API_KEY", "k")
        query_embedder = MagicMock()
        query_embedder.embed_query.return_value = _vector("probe")

        with patch("lgrep.embeddings.VoyageEmbedder", return_value=query_embedder):
            rc = _cmd_search_semantic(["return", str(worktree), "--limit", "50"])

        assert rc == 0
        # Structured logs share stdout; the command's JSON is the last line.
        results = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["results"]
        hits = {r["file_path"]: r["content"] for r in results}
        assert set(hits) == {"same.py"}

    @pytest.mark.parametrize("checkout", ["trunk", "worktree"])
    def test_zero_chunk_file_is_indexed_after_it_gains_code(self, tmp_path, monkeypatch, checkout):
        """A file that produced no chunks is embedded once its content changes."""
        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)
        root = repo if checkout == "trunk" else worktree
        (root / "empty.py").write_text("")
        store = self._index(root, embedder)
        assert "empty.py" in store.get_zero_chunk_files()

        (root / "empty.py").write_text("def gained():\n    return 'gained code'\n")
        # A reconcile after the edit (a search, for example) must forget the
        # marker, so the staleness check still sees the file as unindexed.
        from lgrep.indexing import Indexer
        from lgrep.server.lifecycle import ProjectState, _check_staleness

        indexer = Indexer(root, store, embedder)
        indexer.reconcile_checkout()
        assert "empty.py" not in store.get_zero_chunk_files()
        base_path = str(repo) if checkout == "worktree" else None
        state = ProjectState(db=store, indexer=indexer, base_path=base_path)
        assert _check_staleness(state)[0] is True

        embedder.texts.clear()
        store = self._index(root, embedder)
        assert "gained code" in "\n".join(embedder.texts)
        assert "empty.py" in store.get_indexed_files()
        assert "empty.py" not in store.get_zero_chunk_files()

    def test_filter_indexes_keep_overlay_search_correct(self, tmp_path, monkeypatch):
        """Scalar indexes on the prefilter columns persist and keep results exact."""
        from lgrep.storage import ChunkStore, get_project_db_path

        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        trunk = self._index(repo, embedder)
        overlay = self._index(worktree, embedder)
        trunk.prepare_hybrid_indexes(vector_index_row_threshold=0)

        columns = {c for index in trunk.table.list_indices() for c in index.columns}
        assert {"checkout", "file_path"} <= columns
        reopened = ChunkStore(get_project_db_path(repo), project_path=repo)
        reopened.table  # noqa: B018 - opening the table probes its indexes
        assert reopened._filter_indexed

        visible = self._visible(overlay)
        assert set(visible) == {"shared.py", "same.py", "branch_only.py"}
        assert "branch version" in visible["shared.py"]
        assert set(self._visible(trunk)) == {"shared.py", "same.py", "trunk_only.py"}

    def test_trunk_search_excludes_overlay(self, tmp_path, monkeypatch):
        """Trunk search never returns a worktree's overlay rows."""
        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        trunk = self._index(repo, embedder)
        self._index(worktree, embedder)

        visible = self._visible(trunk)
        assert set(visible) == {"shared.py", "same.py", "trunk_only.py"}
        assert "trunk version" in visible["shared.py"]
        assert "branch version" not in visible["shared.py"]
        assert trunk.get_indexed_files() == {"shared.py", "same.py", "trunk_only.py"}
        assert trunk.count_chunks() < trunk.table.count_rows()

        # A trunk pass after the worktree indexed changes and embeds nothing.
        embedder.texts.clear()
        self._index(repo, embedder)
        assert embedder.texts == []

    def test_worktree_embeds_only_diff(self, tmp_path, monkeypatch):
        """A worktree's first index embeds only files that differ from base."""
        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)

        embedder.texts.clear()
        self._index(worktree, embedder)
        embedded = "\n".join(embedder.texts)
        assert "branch version" in embedded
        assert "branch only" in embedded
        assert "unchanged" not in embedded
        assert "trunk" not in embedded

        embedder.texts.clear()
        overlay = self._index(worktree, embedder)
        assert embedder.texts == []

        # A watcher event for a file edited back to its base content drops
        # the overlay and serves base rows again, with no embedding.
        from lgrep.indexing import Indexer
        from lgrep.storage import ChunkStore

        (worktree / "shared.py").write_text(_TRUNK_SHARED)
        Indexer(worktree, overlay, embedder).index_file(worktree / "shared.py")
        assert embedder.texts == []
        assert set(ChunkStore.get_file_hashes(overlay)) == {"branch_only.py"}
        assert "trunk version" in self._visible(overlay)["shared.py"]

    def test_worktree_rechecks_after_base_change(self, tmp_path, monkeypatch):
        """After base rows change, a worktree re-compares every file with base."""
        from lgrep.indexing import Indexer
        from lgrep.server.lifecycle import ProjectState, _check_staleness

        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)
        overlay = self._index(worktree, embedder)
        state = ProjectState(
            db=overlay, indexer=Indexer(worktree, overlay, embedder), base_path=str(repo)
        )
        assert _check_staleness(state) == (False, 0)

        # Trunk moves: shared.py now equals the branch version, same.py changes.
        (repo / "shared.py").write_text(_BRANCH_SHARED)
        (repo / "same.py").write_text("def same():\n    return 'trunk moved'\n")
        self._index(repo, embedder)

        assert overlay.needs_full_recheck()
        assert _check_staleness(state)[0] is True

        embedder.texts.clear()
        overlay = self._index(worktree, embedder)
        # Only same.py differs from base now; shared.py's overlay rows are dropped.
        assert "unchanged" in "\n".join(embedder.texts)
        assert "branch version" not in "\n".join(embedder.texts)
        from lgrep.storage import ChunkStore

        assert set(ChunkStore.get_file_hashes(overlay)) == {"same.py", "branch_only.py"}
        visible = self._visible(overlay)
        assert "unchanged" in visible["same.py"]
        assert "trunk moved" not in visible["same.py"]
        assert "branch version" in visible["shared.py"]
        assert not overlay.needs_full_recheck()

    def test_shared_cache_migrates_without_embedding(self, tmp_path, monkeypatch):
        """A cache from before per-checkout rows becomes base with no embedding."""
        import lancedb

        from lgrep.storage import CHUNKS_TABLE, get_project_db_path

        repo, worktree = self._setup(tmp_path, monkeypatch)
        embedder = _RecordingEmbedder()
        self._index(repo, embedder)
        db_path = get_project_db_path(repo)
        old = lancedb.connect(str(db_path)).open_table(CHUNKS_TABLE)
        old.drop_columns(["checkout"])
        rows = old.count_rows()
        assert "checkout" not in old.schema.names
        self._owners.clear()

        embedder.texts.clear()
        trunk = self._index(repo, embedder)
        assert embedder.texts == []
        assert "checkout" in trunk.table.schema.names
        assert trunk.table.count_rows() == rows
        assert trunk.count_chunks() == rows
        assert "trunk version" in self._visible(trunk)["shared.py"]

        self._index(worktree, embedder)
        embedded = "\n".join(embedder.texts)
        assert "unchanged" not in embedded
        assert "branch version" in embedded

    def test_gc_removes_dead_worktree_overlay(self, tmp_path, monkeypatch):
        """gc deletes overlay rows and state of worktrees that no longer exist."""
        from lgrep.storage import get_project_db_path
        from lgrep.tools.prune_orphans import gc_worktree_meta

        repo, worktree = self._setup(tmp_path, monkeypatch)
        live = tmp_path / "live"
        subprocess.run(["git", "worktree", "add", "-q", str(live)], cwd=repo, check=True)
        (live / "live_only.py").write_text("def live_only():\n    return 1\n")
        embedder = _RecordingEmbedder()
        trunk = self._index(repo, embedder)
        self._index(worktree, embedder)
        self._index(live.resolve(), embedder)
        base_rows = trunk.count_chunks()
        overlay_where = f"checkout = '{worktree}'"
        assert trunk.table.count_rows(overlay_where) > 0
        state_files = list((get_project_db_path(repo) / "overlays").glob("*.json"))
        assert len(state_files) == 2

        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=True
        )

        preview = gc_worktree_meta(cache_dir=tmp_path / "cache", dry_run=True)
        assert preview["overlays_removed"] == 1
        assert trunk.table.count_rows(overlay_where) > 0

        report = gc_worktree_meta(cache_dir=tmp_path / "cache", dry_run=False)
        assert report["overlays_removed"] == 1
        assert report["overlay_rows_removed"] > 0
        trunk.table.checkout_latest()
        assert trunk.table.count_rows(overlay_where) == 0
        assert trunk.table.count_rows(f"checkout = '{live.resolve()}'") > 0
        assert trunk.count_chunks() == base_rows
        assert len(list((get_project_db_path(repo) / "overlays").glob("*.json"))) == 1
