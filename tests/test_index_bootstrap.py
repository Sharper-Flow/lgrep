"""Tests for first-use symbol index bootstrap on unindexed git checkouts.

Covers the approved objective:
- A symbol query against an unindexed linked worktree builds that
  worktree's own index under its own key, seeded from an indexed sibling
  checkout, and answers from the worktree's own files.
- A first query with no indexed sibling runs a full index.
- Creating a new index deletes indexes whose repo_path no longer exists
  and keeps ``github:`` keys.
- Non-git folders and github keys keep the "not indexed" error contract.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from lgrep.storage.index_store import CodeIndex, IndexStore, _repo_key

if TYPE_CHECKING:
    from pathlib import Path


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
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


def _commit_all(cwd: Path, message: str) -> None:
    _git("add", ".", cwd=cwd)
    _git("commit", "-m", message, cwd=cwd)


@pytest.fixture
def split_repo(tmp_path):
    """Trunk repo plus a linked worktree whose file sets differ.

    Returns (trunk, worktree) where:
    - src/shared.py declares authenticate() in both, returning "trunk"
      vs "worktree";
    - src/trunk_only.py is committed to trunk after the worktree was
      created, so only trunk has it;
    - src/worktree_only.py exists only in the worktree (uncommitted).
    """
    trunk = tmp_path / "trunk"
    (trunk / "src").mkdir(parents=True)
    (trunk / "src" / "shared.py").write_text('def authenticate():\n    return "trunk"\n')
    # Identical in both checkouts: carries the call occurrence that
    # search_references matches.
    (trunk / "src" / "usage.py").write_text("def call_auth():\n    return authenticate()\n")
    _git("init", cwd=trunk)
    _commit_all(trunk, "init")
    worktree = tmp_path / "wt"
    _git("worktree", "add", str(worktree), cwd=trunk)
    (trunk / "src" / "trunk_only.py").write_text("def trunk_only_symbol():\n    return 1\n")
    _commit_all(trunk, "trunk-only file")
    (worktree / "src" / "worktree_only.py").write_text(
        "def worktree_only_symbol():\n    return 2\n"
    )
    (worktree / "src" / "shared.py").write_text('def authenticate():\n    return "worktree"\n')
    return trunk, worktree


class TestGitCommonDir:
    def test_linked_worktrees_share_one_common_dir(self, split_repo):
        from lgrep.tools._index_bootstrap import git_common_dir

        trunk, worktree = split_repo
        assert git_common_dir(trunk) == git_common_dir(worktree)

    def test_non_git_dir_returns_none(self, tmp_path):
        from lgrep.tools._index_bootstrap import git_common_dir

        plain = tmp_path / "plain"
        plain.mkdir()
        assert git_common_dir(plain) is None


class TestSeededFirstQuery:
    """First query against an unindexed linked worktree of an indexed trunk."""

    def test_search_symbols_builds_worktree_key_and_answers_from_worktree(
        self, split_repo, tmp_path
    ):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        trunk, worktree = split_repo
        store_dir = tmp_path / "store"

        assert "error" not in index_folder(str(trunk), storage_dir=store_dir)

        found = search_symbols("worktree_only_symbol", str(worktree), storage_dir=store_dir)

        assert "error" not in found
        assert found["total_matches"] == 1
        assert found["index_refreshed"] is True

        store = IndexStore(storage_dir=store_dir)
        trunk_index = store.load(str(trunk.resolve()))
        worktree_index = store.load(str(worktree.resolve()))
        # Each checkout holds its own index under its own key.
        assert trunk_index is not None
        assert worktree_index is not None
        assert _repo_key(str(trunk.resolve())) != _repo_key(str(worktree.resolve()))
        # The worktree index describes the worktree's own file set.
        assert "src/worktree_only.py" in worktree_index.files
        assert "src/trunk_only.py" not in worktree_index.files
        # A trunk-only symbol is not served from the trunk's index.
        miss = search_symbols("trunk_only_symbol", str(worktree), storage_dir=store_dir)
        assert "error" not in miss
        assert miss["total_matches"] == 0

    def test_get_symbol_serves_worktree_body_after_seed(self, split_repo, tmp_path):
        from lgrep.tools.get_symbol import get_symbol
        from lgrep.tools.index_folder import index_folder

        trunk, worktree = split_repo
        store_dir = tmp_path / "store"

        assert "error" not in index_folder(str(trunk), storage_dir=store_dir)

        result = get_symbol(
            "src/shared.py:function:authenticate", str(worktree), storage_dir=store_dir
        )

        assert "error" not in result
        assert 'return "worktree"' in result["symbol"]["source"]

    def test_get_symbols_bootstraps_unindexed_worktree(self, split_repo, tmp_path):
        from lgrep.tools.get_symbol import get_symbols
        from lgrep.tools.index_folder import index_folder

        trunk, worktree = split_repo
        store_dir = tmp_path / "store"

        assert "error" not in index_folder(str(trunk), storage_dir=store_dir)

        result = get_symbols(
            ["src/worktree_only.py:function:worktree_only_symbol"],
            str(worktree),
            storage_dir=store_dir,
        )

        assert "error" not in result
        assert result["symbols"][0].get("error") != "not_found"

    def test_search_references_bootstraps_unindexed_worktree(self, split_repo, tmp_path):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_references import search_references

        trunk, worktree = split_repo
        store_dir = tmp_path / "store"

        assert "error" not in index_folder(str(trunk), storage_dir=store_dir)

        result = search_references("authenticate", str(worktree), storage_dir=store_dir)

        assert "error" not in result
        assert result["total_matches"] >= 1
        assert all(row["file_path"].startswith("src/") for row in result["results"])


class TestUnseededFullIndex:
    def test_first_query_without_indexed_sibling_runs_full_index(self, split_repo, tmp_path):
        from lgrep.tools.search_symbols import search_symbols

        _, worktree = split_repo
        store_dir = tmp_path / "store"

        found = search_symbols("worktree_only_symbol", str(worktree), storage_dir=store_dir)

        assert "error" not in found
        assert found["total_matches"] == 1
        store = IndexStore(storage_dir=store_dir)
        assert store.list_repos() == [str(worktree.resolve())]


class TestGonePathsPrunedOnCreate:
    def test_creating_an_index_deletes_gone_path_indexes_and_keeps_github(
        self, split_repo, tmp_path
    ):
        from lgrep.tools.index_folder import index_folder
        from lgrep.tools.search_symbols import search_symbols

        trunk, worktree = split_repo
        store_dir = tmp_path / "store"
        store = IndexStore(storage_dir=store_dir)

        assert "error" not in index_folder(str(trunk), storage_dir=store_dir)

        # A second indexed checkout that is then deleted from disk.
        doomed = tmp_path / "doomed"
        (doomed / "src").mkdir(parents=True)
        (doomed / "src" / "a.py").write_text("def doomed_symbol():\n    return 1\n")
        _git("init", cwd=doomed)
        _commit_all(doomed, "init")
        assert "error" not in index_folder(str(doomed), storage_dir=store_dir)
        doomed_key = str(doomed.resolve())
        assert store.load(doomed_key) is not None
        shutil.rmtree(doomed)

        # A remote key has no local path at all and must survive the sweep.
        store.save(
            CodeIndex(
                repo_path="github:owner/repo@main",
                files={"a.py": "h"},
                symbols={
                    "a.py:function:f": {
                        "id": "a.py:function:f",
                        "name": "f",
                        "kind": "function",
                        "file_path": "a.py",
                        "start_byte": 0,
                        "end_byte": 1,
                    }
                },
            )
        )

        # First query against the unindexed worktree creates a new index.
        found = search_symbols("worktree_only_symbol", str(worktree), storage_dir=store_dir)
        assert "error" not in found

        assert store.load(doomed_key) is None
        assert store.load("github:owner/repo@main") is not None
        assert store.load(str(worktree.resolve())) is not None
        assert store.load(str(trunk.resolve())) is not None


class TestNonGitContractPreserved:
    def test_plain_folder_still_reports_not_indexed(self, tmp_path):
        from lgrep.tools.search_symbols import search_symbols

        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "auth.py").write_text("def authenticate():\n    return 1\n")

        result = search_symbols("authenticate", str(plain), storage_dir=tmp_path / "store")

        assert "error" in result
        assert "Repository not indexed" in result["error"]

    def test_github_key_still_reports_not_indexed(self, tmp_path):
        from lgrep.tools.search_symbols import search_symbols

        result = search_symbols("f", "github:owner/repo@main", storage_dir=tmp_path / "store")

        assert "error" in result
        assert "Repository not indexed" in result["error"]


class TestSeedFrom:
    def test_seed_copy_lands_under_target_key_with_target_sidecar(self, tmp_path):
        store_dir = tmp_path / "store"
        store = IndexStore(storage_dir=store_dir)
        store.save(
            CodeIndex(
                repo_path=str(tmp_path / "seedrepo"),
                files={"a.py": "h1"},
                symbols={
                    "a.py:function:f": {
                        "id": "a.py:function:f",
                        "name": "f",
                        "kind": "function",
                        "file_path": "a.py",
                        "start_byte": 0,
                        "end_byte": 1,
                    }
                },
            )
        )

        target = str(tmp_path / "targetrepo")
        assert store.seed_from(str(tmp_path / "seedrepo"), target) is True

        copied = store.load(target)
        assert copied is not None
        assert copied.files == {"a.py": "h1"}
        # list_repos keys the copy by the TARGET path even before the
        # caller's refresh rewrites the body.
        assert target in store.list_repos()

    def test_seed_from_missing_source_returns_false(self, tmp_path):
        store = IndexStore(storage_dir=tmp_path / "store")

        assert store.seed_from(str(tmp_path / "never"), str(tmp_path / "target")) is False
        assert store.load(str(tmp_path / "target")) is None
