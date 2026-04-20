"""Tests to verify the packaged dependencies and version metadata.

These tests serve as the TDD red phase for tk-jD3Lki16:
- Write failing tests first (deps not yet in pyproject.toml)
- Add deps to pyproject.toml
- Tests go green
"""


def test_tree_sitter_language_pack_importable():
    """tree-sitter-language-pack must be importable after adding to deps."""
    import tree_sitter_language_pack  # noqa: F401

    # Verify the key API is available
    assert hasattr(tree_sitter_language_pack, "get_parser")
    assert hasattr(tree_sitter_language_pack, "get_language")


def test_pathspec_importable():
    """pathspec must be importable after adding to deps."""
    import pathspec  # noqa: F401

    # Verify the key API is available
    assert hasattr(pathspec, "PathSpec")
    assert hasattr(pathspec, "patterns")


def test_version_is_3_0_0():
    """lgrep package version should match the current release."""
    from lgrep import __version__

    assert __version__ == "3.0.0", f"Expected 3.0.0, got {__version__}"


def test_tree_sitter_language_pack_python_parser():
    """tree-sitter-language-pack must provide a working Python parser."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser("python")
    assert parser is not None

    # Parse a trivial Python snippet
    tree = parser.parse(b"def hello(): pass")
    assert tree is not None
    assert tree.root_node is not None
    assert tree.root_node.type == "module"


def test_pathspec_gitignore_pattern():
    """pathspec must correctly match gitignore-style patterns."""
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitignore", ["*.pyc", "node_modules/", "__pycache__/"])
    assert spec.match_file("foo.pyc")
    assert spec.match_file("node_modules/lodash/index.js")
    assert not spec.match_file("src/main.py")
