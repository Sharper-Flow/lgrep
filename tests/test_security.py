"""Security tests for discovery.py (tk-ypctQFYt).

RED phase: tests fail before security hardening is applied.
GREEN phase: all pass after implementation.

≥6 adversarial scenarios required.
"""

import os
import tempfile
from pathlib import Path

import pytest


class TestPathTraversalSecurity:
    """Path traversal attacks must be rejected."""

    def test_path_outside_root_is_ignored(self, tmp_path):
        """Files outside the root directory must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        (root / "safe.py").write_text("print('hello')")

        discovery = FileDiscovery(root)

        # Path traversal attempt
        outside_path = tmp_path / "secret.py"
        outside_path.write_text("SECRET=password123")

        assert discovery.is_ignored(outside_path)

    def test_dotdot_path_is_ignored(self, tmp_path):
        """Paths with .. components that escape root must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()

        discovery = FileDiscovery(root)

        # Construct a path that would escape root via ..
        escape_path = root / ".." / "escape.py"
        assert discovery.is_ignored(escape_path)


class TestSymlinkSecurity:
    """Symlinks that escape the root must be rejected."""

    def test_symlink_escaping_root_is_ignored(self, tmp_path):
        """Symlinks pointing outside the project root must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()

        # Create a file outside the root
        outside = tmp_path / "outside_secret.py"
        outside.write_text("SECRET=password")

        # Create a symlink inside root pointing outside
        symlink = root / "evil_link.py"
        symlink.symlink_to(outside)

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(symlink), "Symlink escaping root must be ignored"

    def test_symlink_within_root_is_allowed(self, tmp_path):
        """Symlinks pointing within the project root should be allowed."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()

        # Create a real file inside root
        real_file = root / "real.py"
        real_file.write_text("def foo(): pass")

        # Create a symlink inside root pointing to another file inside root
        symlink = root / "link.py"
        symlink.symlink_to(real_file)

        discovery = FileDiscovery(root)
        # Symlink within root should NOT be ignored
        assert not discovery.is_ignored(symlink), "Symlink within root should be allowed"


class TestSecretFileDetection:
    """Secret files must be excluded from discovery."""

    def test_dotenv_file_is_ignored(self, tmp_path):
        """'.env' files must be ignored (contain secrets)."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        env_file = root / ".env"
        env_file.write_text("DATABASE_URL=postgres://user:pass@host/db\nSECRET_KEY=abc123")

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(env_file), ".env file must be ignored"

    def test_dotenv_local_file_is_ignored(self, tmp_path):
        """'.env.local' files must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        env_file = root / ".env.local"
        env_file.write_text("SECRET=local_secret")

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(env_file), ".env.local file must be ignored"

    def test_pem_file_is_ignored(self, tmp_path):
        """'.pem' certificate/key files must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        pem_file = root / "server.pem"
        pem_file.write_text(
            "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC\n-----END PRIVATE KEY-----"
        )

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(pem_file), ".pem file must be ignored"

    def test_credentials_json_is_ignored(self, tmp_path):
        """'credentials.json' files must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        creds_file = root / "credentials.json"
        creds_file.write_text(
            '{"type": "service_account", "private_key": "-----BEGIN RSA PRIVATE KEY-----"}'
        )

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(creds_file), "credentials.json must be ignored"


class TestBinaryFileDetection:
    """Binary files must be excluded from discovery."""

    def test_binary_file_is_ignored(self, tmp_path):
        """Files with binary content (null bytes) must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        binary_file = root / "compiled.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe\xfd\xfc" * 100)

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(binary_file), "Binary file must be ignored"

    def test_text_file_is_not_ignored_as_binary(self, tmp_path):
        """Normal text files must NOT be flagged as binary."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        text_file = root / "main.py"
        text_file.write_text("def main():\n    print('hello world')\n")

        discovery = FileDiscovery(root)
        assert not discovery.is_ignored(text_file), "Text file must not be ignored as binary"


class TestFileSizeCap:
    """Files exceeding the size cap must be excluded."""

    def test_oversized_file_is_ignored(self, tmp_path):
        """Files larger than the size cap must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        big_file = root / "huge.py"
        # Write 2MB of content (default cap should be 1MB or similar)
        big_file.write_bytes(b"x" * (2 * 1024 * 1024))

        discovery = FileDiscovery(root)
        assert discovery.is_ignored(big_file), "Oversized file must be ignored"

    def test_normal_size_file_is_not_ignored(self, tmp_path):
        """Normal-sized files must not be ignored due to size."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        normal_file = root / "normal.py"
        normal_file.write_text("def foo(): pass\n" * 100)

        discovery = FileDiscovery(root)
        assert not discovery.is_ignored(normal_file), "Normal-sized file must not be ignored"


class TestSkipPatterns:
    """Common skip patterns (node_modules, vendor, dist, etc.) must be excluded."""

    def test_node_modules_directory_is_ignored(self, tmp_path):
        """node_modules/ directory must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        nm = root / "node_modules"
        nm.mkdir()
        (nm / "lodash" / "index.js").parent.mkdir(parents=True)
        (nm / "lodash" / "index.js").write_text("module.exports = {};")

        discovery = FileDiscovery(root)
        files = list(discovery.find_files())
        assert not any("node_modules" in str(f) for f in files), (
            "node_modules files must not be discovered"
        )

    def test_vendor_directory_is_ignored(self, tmp_path):
        """vendor/ directory must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        vendor = root / "vendor"
        vendor.mkdir()
        (vendor / "lib.go").write_text("package vendor")

        discovery = FileDiscovery(root)
        files = list(discovery.find_files())
        assert not any("vendor" in str(f) for f in files), "vendor files must not be discovered"

    def test_dist_directory_is_ignored(self, tmp_path):
        """dist/ directory must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        dist = root / "dist"
        dist.mkdir()
        (dist / "bundle.js").write_text("!function(){}")

        discovery = FileDiscovery(root)
        files = list(discovery.find_files())
        assert not any("dist" in str(f) for f in files), "dist files must not be discovered"

    def test_pycache_directory_is_ignored(self, tmp_path):
        """__pycache__/ directory must be ignored."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()
        cache = root / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-311.pyc").write_bytes(b"\x00\x01\x02\x03")

        discovery = FileDiscovery(root)
        files = list(discovery.find_files())
        assert not any("__pycache__" in str(f) for f in files), (
            "__pycache__ files must not be discovered"
        )


class TestFindFilesSecurityIntegration:
    """Integration tests: find_files() must not yield any dangerous files."""

    def test_find_files_excludes_all_secret_types(self, tmp_path):
        """find_files() must exclude .env, .pem, credentials.json in one scan."""
        from lgrep.discovery import FileDiscovery

        root = tmp_path / "project"
        root.mkdir()

        # Safe file
        (root / "main.py").write_text("def main(): pass")

        # Dangerous files
        (root / ".env").write_text("SECRET=abc")
        (root / "server.pem").write_text("-----BEGIN PRIVATE KEY-----")
        (root / "credentials.json").write_text('{"private_key": "secret"}')

        discovery = FileDiscovery(root)
        files = [str(f) for f in discovery.find_files()]

        assert any("main.py" in f for f in files), "main.py should be discovered"
        assert not any(".env" in f for f in files), ".env must not be discovered"
        assert not any(".pem" in f for f in files), ".pem must not be discovered"
        assert not any("credentials.json" in f for f in files), (
            "credentials.json must not be discovered"
        )
