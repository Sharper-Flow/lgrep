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


def _write(root: Path, rel: str, text: str = "source\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _found(discovery: FileDiscovery, root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in discovery.find_files()}


@pytest.mark.parametrize("ignore_name", [".gitignore", ".lgrepignore"])
class TestGitIgnoreParity:
    """Cases where git's directory semantics differ from plain path matching."""

    def test_escaped_trailing_space(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("spaced\\ \r\n")
        _write(tmp_path, "spaced ")
        _write(tmp_path, "spaced")

        discovery = FileDiscovery(tmp_path)

        assert discovery.is_ignored("spaced ") is True
        assert discovery.is_ignored("spaced") is False

    def test_contents_pattern_does_not_exclude_its_directory(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("gen/**\n!gen/keep.py\n")
        _write(tmp_path, "gen/keep.py")
        _write(tmp_path, "gen/drop.py")

        found = _found(FileDiscovery(tmp_path), tmp_path)

        assert "gen/keep.py" in found
        assert "gen/drop.py" not in found

    def test_later_directory_pattern_overrides_earlier_negation(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("!*\n/a/\n")
        _write(tmp_path, "a/b.py")
        _write(tmp_path, "c.py")

        discovery = FileDiscovery(tmp_path)

        assert discovery.is_ignored(tmp_path / "a") is True
        assert _found(discovery, tmp_path) == {"c.py", ignore_name}

    def test_directory_below_excluded_directory_is_excluded(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("/a/\n!a/b/\n")
        _write(tmp_path, "a/b/c.py")

        assert FileDiscovery(tmp_path).is_ignored(tmp_path / "a" / "b") is True

    def test_invalid_patterns_are_skipped(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("!\ntrailing\\\nbuild/\n")
        _write(tmp_path, "build/out.py")
        _write(tmp_path, "src/main.py")

        found = _found(FileDiscovery(tmp_path), tmp_path)

        assert "build/out.py" not in found
        assert "src/main.py" in found


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


class TestDefaultTemplateAdvIgnore:
    """Default `.lgrepignore` template must exclude stale ADV change/archive state.

    Indexing `.adv/changes/` and `.adv/archive/` markdown causes stale proposal
    text to outrank current implementation in semantic search results.
    `.adv/specs/` must stay searchable — spec content is useful to agents.
    """

    def test_template_excludes_adv_changes(self):
        assert ".adv/changes/" in DEFAULT_LGREPIGNORE_TEMPLATE

    def test_template_excludes_adv_archive(self):
        assert ".adv/archive/" in DEFAULT_LGREPIGNORE_TEMPLATE

    def test_template_keeps_adv_specs_searchable(self):
        # Neither the specs subtree nor the bare .adv/ root should be excluded.
        assert ".adv/specs/" not in DEFAULT_LGREPIGNORE_TEMPLATE
        # The only `.adv/` lines must be the two specific subdir entries.
        adv_lines = [line for line in DEFAULT_LGREPIGNORE_TEMPLATE.splitlines() if ".adv/" in line]
        assert sorted(adv_lines) == [".adv/archive/", ".adv/changes/"]
