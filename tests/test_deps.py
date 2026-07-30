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


def test_version_matches_pyproject():
    """lgrep package __version__ should match the pyproject.toml version."""
    import tomllib
    from pathlib import Path

    from lgrep import __version__

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    expected = data["project"]["version"]
    assert __version__ == expected, (
        f"src/lgrep/__init__.py:__version__ ({__version__!r}) "
        f"does not match pyproject.toml version ({expected!r}). "
        f"Bump both together when releasing."
    )


def _declared_requirement(name: str):
    """Return the parsed pyproject requirement for ``name``."""
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    matches = [
        Requirement(raw) for raw in data["project"]["dependencies"] if Requirement(raw).name == name
    ]
    assert len(matches) == 1, f"Expected exactly one {name!r} dependency, found {len(matches)}"
    return matches[0]


def test_mcp_dependency_excludes_unmigrated_major():
    """The mcp requirement must exclude 2.x until the server is migrated.

    mcp 2.0.0 removed ``mcp.server.fastmcp``, which ``lgrep.server`` imports, so an
    unbounded requirement lets a fresh install resolve a release that cannot start
    the server at all. Upstream recommends an explicit ``<2`` bound until migration.
    """
    requirement = _declared_requirement("mcp")

    incompatible = [
        version
        for version in ("2.0.0", "2.1.0", "3.0.0")
        if requirement.specifier.contains(version, prereleases=True)
    ]
    assert not incompatible, (
        f"pyproject.toml dependency {str(requirement)!r} admits unmigrated "
        f"mcp release(s) {incompatible}. lgrep.server imports mcp.server.fastmcp, "
        f"which mcp 2.x removed. Keep an upper bound (for example 'mcp>=1.28,<2') "
        f"until the MCPServer migration lands."
    )


def test_mcp_dependency_still_admits_supported_release():
    """The bound must not be so tight that the proven-good release is excluded."""
    requirement = _declared_requirement("mcp")

    assert requirement.specifier.contains("1.28.1", prereleases=True), (
        f"pyproject.toml dependency {str(requirement)!r} excludes mcp 1.28.1, "
        f"the release the server is verified against."
    )


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
