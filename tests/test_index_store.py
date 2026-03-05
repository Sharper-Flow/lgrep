"""Tests for index_store.py (tk-DgoODi8l).

RED phase: tests fail before index_store.py exists.
GREEN phase: all pass after implementation.

Covers:
- CodeIndex dataclass
- IndexStore atomic save/load
- Byte-offset symbol content retrieval
- File hash tracking
- Incremental change detection (changed/new/deleted)
- list_repos, delete_index
- Path traversal safety
"""

import textwrap

PYTHON_FIXTURE = textwrap.dedent("""\
    def authenticate(user, password):
        return True

    class UserService:
        def get_user(self, user_id):
            pass
""")


class TestIndexStoreImport:
    """IndexStore must be importable with the expected API."""

    def test_index_store_importable(self):
        """IndexStore must be importable."""
        from lgrep.storage.index_store import IndexStore  # noqa: F401

    def test_code_index_importable(self):
        """CodeIndex must be importable."""
        from lgrep.storage.index_store import CodeIndex  # noqa: F401


class TestCodeIndex:
    """CodeIndex dataclass must have the right structure."""

    def test_code_index_has_required_fields(self):
        """CodeIndex must have repo_path, files, and symbols fields."""
        from lgrep.storage.index_store import CodeIndex

        idx = CodeIndex(
            repo_path="/path/to/repo",
            files={},
            symbols={},
        )
        assert idx.repo_path == "/path/to/repo"
        assert idx.files == {}
        assert idx.symbols == {}


class TestIndexStoreAtomicSaveLoad:
    """IndexStore must save and load atomically."""

    def test_save_creates_file(self, tmp_path):
        """save() must create the index file."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(repo_path="/repo", files={}, symbols={})
        store.save(idx)

        # File must exist
        assert any(tmp_path.iterdir()), "save() must create at least one file"

    def test_load_returns_saved_index(self, tmp_path):
        """load() must return the same index that was saved."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo",
            files={"src/auth.py": "abc123"},
            symbols={
                "src/auth.py:function:authenticate": {"name": "authenticate", "kind": "function"}
            },
        )
        store.save(idx)

        loaded = store.load("/repo")
        assert loaded is not None
        assert loaded.repo_path == "/repo"
        assert "src/auth.py" in loaded.files
        assert "src/auth.py:function:authenticate" in loaded.symbols

    def test_load_returns_none_for_missing_repo(self, tmp_path):
        """load() must return None for a repo that hasn't been indexed."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)
        result = store.load("/nonexistent/repo")
        assert result is None

    def test_save_is_atomic(self, tmp_path):
        """save() must use write-to-temp+rename for atomicity."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(repo_path="/repo", files={}, symbols={})
        store.save(idx)

        # No .tmp files should remain after save
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Temp files left after save: {tmp_files}"


class TestFileHashTracking:
    """IndexStore must track file hashes for incremental change detection."""

    def test_file_hash_stored(self, tmp_path):
        """File hashes must be stored in the index."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo",
            files={"src/auth.py": "sha256:abc123"},
            symbols={},
        )
        store.save(idx)

        loaded = store.load("/repo")
        assert loaded.files["src/auth.py"] == "sha256:abc123"


class TestIncrementalChangeDetection:
    """IndexStore must detect changed/new/deleted files."""

    def test_detect_new_files(self, tmp_path):
        """Files not in the index must be detected as new."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(repo_path="/repo", files={}, symbols={})
        store.save(idx)

        # Simulate a new file
        current_files = {"src/new.py": "sha256:newfile"}
        changes = store.detect_changes("/repo", current_files)

        assert "src/new.py" in changes["new"]

    def test_detect_changed_files(self, tmp_path):
        """Files with different hashes must be detected as changed."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo",
            files={"src/auth.py": "sha256:old"},
            symbols={},
        )
        store.save(idx)

        current_files = {"src/auth.py": "sha256:new"}
        changes = store.detect_changes("/repo", current_files)

        assert "src/auth.py" in changes["changed"]

    def test_detect_deleted_files(self, tmp_path):
        """Files in the index but not in current_files must be detected as deleted."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo",
            files={"src/old.py": "sha256:abc"},
            symbols={},
        )
        store.save(idx)

        current_files = {}  # old.py is gone
        changes = store.detect_changes("/repo", current_files)

        assert "src/old.py" in changes["deleted"]

    def test_unchanged_files_not_in_changes(self, tmp_path):
        """Files with matching hashes must not appear in any change category."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo",
            files={"src/stable.py": "sha256:same"},
            symbols={},
        )
        store.save(idx)

        current_files = {"src/stable.py": "sha256:same"}
        changes = store.detect_changes("/repo", current_files)

        assert "src/stable.py" not in changes.get("new", [])
        assert "src/stable.py" not in changes.get("changed", [])
        assert "src/stable.py" not in changes.get("deleted", [])


class TestListRepos:
    """list_repos() must return all indexed repo paths."""

    def test_list_repos_empty_initially(self, tmp_path):
        """list_repos() must return [] when no repos are indexed."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)
        assert store.list_repos() == []

    def test_list_repos_returns_saved_repos(self, tmp_path):
        """list_repos() must return all saved repo paths."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        store.save(CodeIndex(repo_path="/repo/a", files={}, symbols={}))
        store.save(CodeIndex(repo_path="/repo/b", files={}, symbols={}))

        repos = store.list_repos()
        assert "/repo/a" in repos
        assert "/repo/b" in repos


class TestDeleteIndex:
    """delete_index() must remove a repo's index."""

    def test_delete_removes_index(self, tmp_path):
        """delete_index() must make load() return None."""
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        store.save(CodeIndex(repo_path="/repo", files={}, symbols={}))

        store.delete_index("/repo")

        assert store.load("/repo") is None

    def test_delete_nonexistent_is_noop(self, tmp_path):
        """delete_index() on a non-existent repo must not raise."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)
        # Must not raise
        store.delete_index("/nonexistent/repo")


class TestByteOffsetRetrieval:
    """IndexStore must support byte-offset symbol content retrieval."""

    def test_get_symbol_content_returns_source(self, tmp_path):
        """get_symbol_content() must return the source bytes for a symbol."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)

        # Create a source file
        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)
        content = src_file.read_bytes()

        # Find the byte range of 'authenticate' function manually
        start = content.index(b"def authenticate")
        end = content.index(b"\nclass")

        result = store.get_symbol_content(src_file, start, end)
        assert result is not None
        assert b"authenticate" in result

    def test_get_symbol_content_missing_file_returns_none(self, tmp_path):
        """get_symbol_content() must return None for missing files."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)
        result = store.get_symbol_content(tmp_path / "missing.py", 0, 100)
        assert result is None


class TestPathTraversalSafety:
    """IndexStore must reject path traversal attempts."""

    def test_safe_content_path_rejects_traversal(self, tmp_path):
        """_safe_content_path must reject paths with .. components."""
        from lgrep.storage.index_store import IndexStore

        store = IndexStore(storage_dir=tmp_path)

        # Attempt path traversal
        evil_path = tmp_path / ".." / "etc" / "passwd"
        result = store.get_symbol_content(evil_path, 0, 100)
        # Must return None (rejected) rather than reading the file
        assert result is None
