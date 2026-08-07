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


class TestConcurrentSaveIntegrity:
    """save() must never commit a torn index under concurrent writers (AC8).

    The pre-fix implementation derived its temp path as
    ``target.with_suffix(".tmp")`` -- a DETERMINISTIC name. Two writers for the
    same repo key therefore shared one temp file, interleaved their bytes into
    it, and then renamed a corrupted blob into place. Atomic rename protects a
    reader from seeing a partial file; it does not protect two writers sharing
    one temp path.

    These tests must FAIL against the deterministic-temp implementation.
    """

    @staticmethod
    def _payload(marker: str, n_symbols: int = 4000) -> dict:
        """Build a symbol map large enough that concurrent writes reliably tear.

        A small payload can be written in a single syscall and would let the
        race pass by luck, so the test would not be a real RED.
        """
        return {
            f"src/{marker}_{i}.py:function:fn_{i}": {
                "id": f"src/{marker}_{i}.py:function:fn_{i}",
                "name": f"fn_{i}",
                "kind": "function",
                "file_path": f"src/{marker}_{i}.py",
                "marker": marker,
                "docstring": marker * 40,
            }
            for i in range(n_symbols)
        }

    def test_concurrent_threaded_saves_never_commit_torn_index(self, tmp_path):
        """Concurrent same-key saves from threads must leave a parseable index."""
        import json
        import threading

        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        repo = "/repo/contended"
        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def writer(marker: str) -> None:
            idx = CodeIndex(
                repo_path=repo,
                files={f"src/{marker}.py": marker * 8},
                symbols=self._payload(marker),
            )
            try:
                barrier.wait(timeout=30)
                for _ in range(5):
                    store.save(idx)
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(m,)) for m in ("alpha", "bravo", "charlie", "delta")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert not errors, f"save() raised under concurrency: {errors}"

        index_files = [p for p in tmp_path.glob("index_*.json") if ".meta." not in p.name]
        assert len(index_files) == 1, f"expected exactly one index, got {index_files}"

        # The committed file must be exactly ONE writer's complete output.
        raw = index_files[0].read_text(encoding="utf-8")
        data = json.loads(raw)  # torn write raises JSONDecodeError here
        assert data["repo_path"] == repo

        markers = {sym["marker"] for sym in data["symbols"].values()}
        assert len(markers) == 1, f"index mixes output from multiple writers: {sorted(markers)}"
        assert len(data["symbols"]) == 4000, "index is structurally incomplete"

        # load() must also succeed on the committed artifact.
        fresh = IndexStore(storage_dir=tmp_path)
        loaded = fresh.load(repo)
        assert loaded is not None
        assert len(loaded.symbols) == 4000

    def test_concurrent_subprocess_saves_never_commit_torn_index(self, tmp_path):
        """Same guarantee across PROCESSES, where no in-process lock could help."""
        import json
        import subprocess
        import sys
        import textwrap

        repo = "/repo/contended-proc"
        script = textwrap.dedent(
            """
            import sys
            from lgrep.storage.index_store import CodeIndex, IndexStore

            storage_dir, repo, marker = sys.argv[1], sys.argv[2], sys.argv[3]
            symbols = {
                f"src/{marker}_{i}.py:function:fn_{i}": {
                    "id": f"src/{marker}_{i}.py:function:fn_{i}",
                    "name": f"fn_{i}",
                    "kind": "function",
                    "file_path": f"src/{marker}_{i}.py",
                    "marker": marker,
                    "docstring": marker * 40,
                }
                for i in range(4000)
            }
            store = IndexStore(storage_dir=storage_dir)
            idx = CodeIndex(repo_path=repo, files={}, symbols=symbols)
            for _ in range(5):
                store.save(idx)
            """
        )
        script_path = tmp_path / "_writer.py"
        script_path.write_text(script, encoding="utf-8")
        store_dir = tmp_path / "store"
        store_dir.mkdir()

        # The child does not inherit the test runner's sys.path, so hand it
        # over explicitly; otherwise `import lgrep` fails in the subprocess.
        import os as _os

        child_env = dict(_os.environ)
        child_env["PYTHONPATH"] = _os.pathsep.join(p for p in sys.path if p)

        procs = [
            subprocess.Popen(  # noqa: S603
                [sys.executable, str(script_path), str(store_dir), repo, marker],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
            )
            for marker in ("echo", "foxtrot", "golf", "hotel")
        ]
        for p in procs:
            out, err = p.communicate(timeout=180)
            assert p.returncode == 0, f"writer failed: {err.decode()[:500]}"

        index_files = [p for p in store_dir.glob("index_*.json") if ".meta." not in p.name]
        assert len(index_files) == 1, f"expected exactly one index, got {index_files}"

        data = json.loads(index_files[0].read_text(encoding="utf-8"))
        markers = {sym["marker"] for sym in data["symbols"].values()}
        assert len(markers) == 1, f"index mixes output from multiple writers: {sorted(markers)}"
        assert len(data["symbols"]) == 4000, "index is structurally incomplete"

    def test_save_uses_unique_temp_path_per_writer(self, tmp_path):
        """Temp paths must not be derivable from the target alone.

        Guards the ROOT CAUSE directly, so a future refactor cannot silently
        reintroduce a shared temp path while the timing tests pass by luck.
        """
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        seen: list[str] = []

        real_replace = __import__("os").replace

        def spy(src, dst):
            seen.append(str(src))
            return real_replace(src, dst)

        import lgrep.storage.index_store as mod

        original = getattr(mod, "os").replace
        try:
            mod.os.replace = spy
            for i in range(3):
                store.save(CodeIndex(repo_path="/repo/x", files={}, symbols={f"s{i}": {}}))
        finally:
            mod.os.replace = original

        index_temps = [s for s in seen if s.endswith(".tmp")]
        assert len(index_temps) >= 3, f"expected temp writes, saw {seen}"
        assert len(set(index_temps)) == len(index_temps), (
            f"temp path is shared between writers (root cause of the torn-write race): {index_temps}"
        )


class TestSidecarWrite:
    """save() must emit index_{key}.meta.json beside every index (AC3/AC7 foundation).

    The sidecar carries the one load-bearing field (repo_path) plus
    informational counts so list_repos() never needs to parse the index body.
    """

    def test_save_writes_sidecar_with_repo_path_and_counts(self, tmp_path):
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        idx = CodeIndex(
            repo_path="/repo/with-sidecar",
            files={"src/a.py": "h1", "src/b.py": "h2"},
            symbols={"s1": {"name": "s1"}, "s2": {"name": "s2"}, "s3": {"name": "s3"}},
            occurrences={"foo": [{"file_path": "src/a.py"}]},
        )
        store.save(idx)

        import hashlib
        import json

        key = hashlib.sha256("/repo/with-sidecar".encode()).hexdigest()[:16]
        meta = tmp_path / f"index_{key}.meta.json"
        assert meta.is_file(), f"sidecar not written: {sorted(p.name for p in tmp_path.iterdir())}"

        sidecar = json.loads(meta.read_text(encoding="utf-8"))
        assert sidecar["repo_path"] == "/repo/with-sidecar"
        assert sidecar["files"] == 2
        assert sidecar["symbols"] == 3
        assert sidecar["occurrences"] == 1
        assert sidecar["meta_version"] == 1
        assert sidecar["version"] == idx.version
        assert isinstance(sidecar["updated_at"], float | int)

    def test_index_written_before_sidecar(self, tmp_path):
        """Crash ordering: index must be committed BEFORE the sidecar.

        Sidecar-first would leave sidecar-without-index, a state the reader
        cannot handle. Index-first leaves index-without-sidecar, which falls
        back to the existing parse path.
        """
        import os

        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        targets: list[str] = []

        real_replace = os.replace

        def spy(src, dst):
            targets.append(str(dst))
            return real_replace(src, dst)

        import lgrep.storage.index_store as mod

        original = mod.os.replace
        try:
            mod.os.replace = spy
            store.save(CodeIndex(repo_path="/repo/ordered", files={}, symbols={"s": {}}))
        finally:
            mod.os.replace = original

        index_targets = [t for t in targets if t.endswith(".json") and ".meta.json" not in t]
        sidecar_targets = [t for t in targets if t.endswith(".meta.json")]
        assert len(index_targets) == 1, targets
        assert len(sidecar_targets) == 1, targets
        assert targets.index(index_targets[0]) < targets.index(sidecar_targets[0]), (
            f"sidecar committed before index: {targets}"
        )

    def test_sidecar_temp_name_does_not_collide_with_index_temp(self, tmp_path):
        """Both writes stage via writer-unique temps; the names must differ."""
        from lgrep.storage.index_store import CodeIndex, IndexStore, _unique_temp_path

        key = "0123456789abcdef"
        index_target = tmp_path / f"index_{key}.json"
        sidecar_target = tmp_path / f"index_{key}.meta.json"
        it = _unique_temp_path(index_target)
        st = _unique_temp_path(sidecar_target)
        assert it.name != st.name
        assert ".meta." in st.name


class TestCompactSerialization:
    """Newly written indexes must be compact (AC7).

    Measured 19.1% of stored bytes were pretty-print whitespace on the real
    2.5GB store (~480MB). Read path is whitespace-agnostic, so existing
    pretty-printed indexes must keep loading unchanged.
    """

    def test_new_index_is_compact(self, tmp_path):
        from lgrep.storage.index_store import CodeIndex, IndexStore

        store = IndexStore(storage_dir=tmp_path)
        store.save(CodeIndex(repo_path="/repo/compact", files={"a": "1"}, symbols={"s": {}}))

        index_files = [p for p in tmp_path.glob("index_*.json") if ".meta.json" not in p.name]
        assert len(index_files) == 1
        raw = index_files[0].read_text(encoding="utf-8")
        assert "\n  " not in raw, f"index still contains indent padding: {raw[:200]!r}"

    def test_pretty_printed_legacy_index_still_loads(self, tmp_path):
        import hashlib
        import json

        from lgrep.storage.index_store import IndexStore

        key = hashlib.sha256("/repo/legacy".encode()).hexdigest()[:16]
        legacy = tmp_path / f"index_{key}.json"
        legacy.write_text(
            json.dumps(
                {
                    "repo_path": "/repo/legacy",
                    "files": {"a.py": "h"},
                    "symbols": {"s": {"kind": "function"}},
                    "occurrences": {},
                    "version": "2.0",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        store = IndexStore(storage_dir=tmp_path)
        loaded = store.load("/repo/legacy")
        assert loaded is not None
        assert loaded.repo_path == "/repo/legacy"
        assert loaded.files == {"a.py": "h"}
