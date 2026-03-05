"""Tests for file discovery."""

import tempfile
from pathlib import Path

import pytest

from lgrep.discovery import DEFAULT_LGREPIGNORE_TEMPLATE, FileDiscovery, scaffold_lgrepignore


@pytest.fixture
def temp_project():
    """Create a temporary project structure with gitignore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create some files
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')")
        (root / "src" / "utils.py").write_text("def util(): pass")

        (root / "tests").mkdir()
        (root / "tests" / "test_main.py").write_text("def test(): pass")

        (root / "node_modules").mkdir()
        (root / "node_modules" / "index.js").write_text("// dummy")

        (root / "dist").mkdir()
        (root / "dist" / "bundle.js").write_text("// dummy")

        # Create .gitignore
        (root / ".gitignore").write_text("node_modules/\ndist/\n*.pyc\n")

        # Create .lgrepignore
        (root / ".lgrepignore").write_text("tests/\n")

        yield root


class TestFileDiscovery:
    """Tests for FileDiscovery class."""

    def test_find_files_respects_gitignore(self, temp_project):
        """Should find files not ignored by .gitignore."""
        discovery = FileDiscovery(temp_project)
        files = list(discovery.find_files())

        # Relative paths
        rel_files = [str(f.relative_to(temp_project)) for f in files]

        assert "src/main.py" in rel_files
        assert "src/utils.py" in rel_files
        assert ".gitignore" in rel_files

        # Ignored files
        assert "node_modules/index.js" not in rel_files
        assert "dist/bundle.js" not in rel_files

    def test_find_files_respects_lgrepignore(self, temp_project):
        """Should find files not ignored by .lgrepignore."""
        discovery = FileDiscovery(temp_project)
        files = list(discovery.find_files())

        rel_files = [str(f.relative_to(temp_project)) for f in files]

        # src should be there
        assert "src/main.py" in rel_files

        # tests should be ignored by .lgrepignore
        assert "tests/test_main.py" not in rel_files

    def test_is_ignored(self, temp_project):
        """Should correctly identify ignored files."""
        discovery = FileDiscovery(temp_project)

        assert discovery.is_ignored(temp_project / "src" / "main.py") is False
        assert discovery.is_ignored(temp_project / "node_modules" / "index.js") is True
        assert discovery.is_ignored(temp_project / "tests" / "test_main.py") is True


class TestLgrepignoreScaffold:
    def test_scaffold_creates_default_file(self, tmp_path):
        path, created = scaffold_lgrepignore(tmp_path)

        assert created is True
        assert path == tmp_path / ".lgrepignore"
        assert path.read_text() == DEFAULT_LGREPIGNORE_TEMPLATE

    def test_scaffold_does_not_overwrite_without_force(self, tmp_path):
        existing = tmp_path / ".lgrepignore"
        existing.write_text("custom-ignore\n")

        path, created = scaffold_lgrepignore(tmp_path)

        assert created is False
        assert path == existing
        assert existing.read_text() == "custom-ignore\n"

    def test_scaffold_overwrites_with_force(self, tmp_path):
        existing = tmp_path / ".lgrepignore"
        existing.write_text("custom-ignore\n")

        path, created = scaffold_lgrepignore(tmp_path, force=True)

        assert created is True
        assert path == existing
        assert existing.read_text() == DEFAULT_LGREPIGNORE_TEMPLATE
