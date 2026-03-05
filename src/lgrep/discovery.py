"""File discovery for lgrep.

Walks directory trees and identifies files to index, respecting
.gitignore and .lgrepignore patterns.

Security hardening (v2.0.0):
- Path traversal validation (files outside root are rejected)
- Symlink escape detection (symlinks pointing outside root are rejected)
- Secret file detection (.env, .pem, credentials.*, etc.)
- Binary file sniffing (null-byte detection)
- Per-file size cap (default 1 MB)
- Skip patterns for common build/dependency directories
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import gitignorefile
import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator

log = structlog.get_logger()

# Maximum file size to index (1 MB). Files larger than this are skipped.
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB

# Directories to always skip (build artifacts, dependencies, generated code).
_SKIP_DIRS: frozenset[str] = frozenset(
    [
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "venv",
        ".venv",
        "env",
        ".env",
        "site-packages",
        ".eggs",
        "*.egg-info",
        "target",  # Rust/Java build output
        "out",
        ".next",
        ".nuxt",
        "coverage",
        ".coverage",
    ]
)

# Secret file patterns — exact filenames or glob-style suffixes.
# These files are excluded regardless of gitignore rules.
_SECRET_FILENAMES: frozenset[str] = frozenset(
    [
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".env.staging",
        "credentials.json",
        "service-account.json",
        "service_account.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
    ]
)

# Secret file suffixes (checked against the full filename).
_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".der",
    ".jks",
    ".keystore",
)

# Prefix patterns for secret files.
_SECRET_PREFIXES: tuple[str, ...] = (
    ".env.",
    "credentials.",
)


def _is_secret_file(path: Path) -> bool:
    """Return True if the file looks like it contains secrets."""
    name = path.name
    # Exact filename match
    if name in _SECRET_FILENAMES:
        return True
    # Suffix match
    if name.endswith(_SECRET_SUFFIXES):
        return True
    # Prefix match
    return bool(any(name.startswith(prefix) for prefix in _SECRET_PREFIXES))


def _is_binary_file(path: Path) -> bool:
    """Return True if the file appears to be binary (contains null bytes).

    Reads only the first 8 KB to keep this fast.
    """
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True  # Unreadable files are treated as binary


def _is_oversized(path: Path) -> bool:
    """Return True if the file exceeds MAX_FILE_SIZE_BYTES."""
    try:
        return path.stat().st_size > MAX_FILE_SIZE_BYTES
    except OSError:
        return True  # Unreadable files are excluded


def _resolves_outside_root(path: Path, root: Path) -> bool:
    """Return True if the path (after resolving symlinks) is outside root."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        return False
    except ValueError:
        return True


class FileDiscovery:
    """Discovers files in a project directory while respecting ignore patterns.

    Combines .gitignore and .lgrepignore rules with security-aware filtering:
    - Rejects paths outside the project root (path traversal protection)
    - Rejects symlinks that escape the root
    - Rejects secret files (.env, .pem, credentials.*, etc.)
    - Rejects binary files (null-byte detection)
    - Rejects oversized files (> 1 MB)
    - Skips common build/dependency directories (node_modules, vendor, dist, etc.)
    """

    def __init__(self, root_path: str | Path) -> None:
        """Initialize file discovery.

        Args:
            root_path: Absolute path to the project root directory
        """
        self.root_path = Path(root_path).resolve()

        # Initialize gitignore matchers
        self.gitignore_match = None
        self.lgrepignore_match = None

        gitignore_path = self.root_path / ".gitignore"
        if gitignore_path.exists():
            self.gitignore_match = gitignorefile.parse(str(gitignore_path))
            log.debug("gitignore_loaded", path=str(gitignore_path))

        lgrepignore_path = self.root_path / ".lgrepignore"
        if lgrepignore_path.exists():
            self.lgrepignore_match = gitignorefile.parse(str(lgrepignore_path))
            log.debug("lgrepignore_loaded", path=str(lgrepignore_path))

        log.info("file_discovery_initialized", root=str(self.root_path))

    def is_ignored(self, path: str | Path) -> bool:
        """Check if a path is ignored by any rules.

        Applies security checks first (path traversal, symlinks, secrets,
        binary, size), then gitignore/lgrepignore rules.

        Args:
            path: Absolute or relative path to check

        Returns:
            True if the path should be ignored
        """
        path = Path(path)

        # Resolve to absolute for security checks — always resolve to eliminate
        # any .. components that could escape the root.
        abs_path = (self.root_path / path).resolve() if not path.is_absolute() else path.resolve()

        # 1. Path traversal: reject anything outside root
        try:
            abs_path.relative_to(self.root_path)
        except ValueError:
            log.debug("security_path_traversal_rejected", path=str(path))
            return True

        # 2. Symlink escape: reject symlinks that resolve outside root
        if abs_path.is_symlink() and _resolves_outside_root(abs_path, self.root_path):
            log.debug("security_symlink_escape_rejected", path=str(path))
            return True

        # 3. Secret file detection
        if _is_secret_file(abs_path):
            log.debug("security_secret_file_rejected", path=str(path))
            return True

        # 4. Skip directory names
        if abs_path.is_dir():
            if abs_path.name in _SKIP_DIRS:
                return True
        else:
            # Check if any parent directory is in the skip list
            try:
                rel = abs_path.relative_to(self.root_path)
                if any(part in _SKIP_DIRS for part in rel.parts[:-1]):
                    return True
            except ValueError:
                return True

        # 5. Binary file detection (only for files, not dirs)
        if abs_path.is_file() and not abs_path.is_symlink():
            if _is_binary_file(abs_path):
                log.debug("security_binary_file_rejected", path=str(path))
                return True

            # 6. File size cap
            if _is_oversized(abs_path):
                log.debug("security_oversized_file_rejected", path=str(path))
                return True

        # 7. Gitignore rules
        str_path = str(abs_path)
        if self.gitignore_match and self.gitignore_match(str_path):
            return True
        if self.lgrepignore_match and self.lgrepignore_match(str_path):
            return True

        # 8. Legacy: always ignore .git directory parts
        try:
            rel = abs_path.relative_to(self.root_path)
            if ".git" in rel.parts:
                return True
        except ValueError:
            return True

        return False

    def find_files(self) -> Iterator[Path]:
        """Iterate over all non-ignored files in the project.

        Yields:
            Absolute paths to discovered files
        """
        for root, dirs, files in os.walk(self.root_path, followlinks=False):
            root_path = Path(root)

            # Filter directories in-place to prevent os.walk from entering them
            orig_dirs = list(dirs)
            dirs[:] = [d for d in dirs if not self.is_ignored(root_path / d)]

            if len(dirs) < len(orig_dirs):
                ignored = set(orig_dirs) - set(dirs)
                log.debug("skipping_directories", root=str(root_path), ignored=list(ignored))

            for file in files:
                file_path = root_path / file
                if not self.is_ignored(file_path):
                    yield file_path
                else:
                    log.debug("skipping_file", path=str(file_path))
