"""File discovery for lgrep.

Walks directory trees and identifies files to index, respecting
.gitignore and .lgrepignore patterns.
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


class FileDiscovery:
    """Discovers files in a project directory while respecting ignore patterns.

    Combines .gitignore and .lgrepignore rules.
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

        Args:
            path: Absolute or relative path to check

        Returns:
            True if the path should be ignored
        """
        path = Path(path)
        if path.is_absolute():
            try:
                path = path.relative_to(self.root_path)
            except ValueError:
                # Path is outside root, ignore it for safety or return True
                return True

        str_path = str(self.root_path / path)

        # Check gitignore
        if self.gitignore_match and self.gitignore_match(str_path):
            return True

        # Check lgrepignore
        if self.lgrepignore_match and self.lgrepignore_match(str_path):
            return True

        # Default ignores (always ignore .git)
        return ".git" in path.parts

    def find_files(self) -> Iterator[Path]:
        """Iterate over all non-ignored files in the project.

        Yields:
            Absolute paths to discovered files
        """
        for root, dirs, files in os.walk(self.root_path):
            root_path = Path(root)

            # Filter directories in-place to prevent os.walk from entering them
            # We must modify 'dirs' in-place
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
