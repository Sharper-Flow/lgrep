"""Tests that package version is derived from release tags via Hatch VCS."""

import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

import pytest
from packaging.version import Version

import lgrep


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _latest_tag() -> str:
    """Return the latest git tag in the repository."""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _wheel_version(wheel_path: Path) -> str:
    """Read the Version metadata from a built wheel."""
    with zipfile.ZipFile(wheel_path) as whl:
        for name in whl.namelist():
            if name.endswith(".dist-info/METADATA"):
                metadata = whl.read(name).decode("utf-8")
                for line in metadata.splitlines():
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
    raise AssertionError("No Version field found in wheel METADATA")


def test_version_base_matches_latest_tag():
    """The package version is derived from the latest release tag."""
    tag = _latest_tag()
    assert tag.startswith("v"), f"unexpected tag format: {tag!r}"
    expected_base = tag[1:]
    actual = Version(lgrep.__version__)
    assert actual.base_version == expected_base, (
        f"lgrep.__version__ base ({actual.base_version!r}) does not match the latest tag "
        f"({expected_base!r}). Hatch VCS should derive the package version from the release tag."
    )


def test_version_matches_package_metadata():
    """The runtime __version__ matches the installed distribution metadata.

    When the imported package is a source checkout (e.g. ``PYTHONPATH=src``),
    the distribution metadata may belong to an unrelated host installation.
    In that case we only verify the runtime version is a valid PEP 440 version.
    """
    Version(lgrep.__version__)

    try:
        dist = importlib.metadata.distribution("lgrep")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("no lgrep distribution metadata available")

    dist_root = Path(dist.locate_file("")).resolve()
    package_root = Path(lgrep.__file__).resolve().parent.parent
    if dist_root == package_root:
        assert lgrep.__version__ == importlib.metadata.version("lgrep")


@pytest.mark.skipif(sys.version_info < (3, 11), reason="importlib.metadata tomllib usage")
def test_wheel_version_matches_tag():
    """Building from a tagged checkout produces a wheel whose version is the tag."""
    pytest.importorskip("build")

    root = _repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()

        # Copy the minimal project files needed to build the package.
        for src_dir in ["src", "skills", "instructions"]:
            if (root / src_dir).exists():
                subprocess.run(["cp", "-r", str(root / src_dir), str(repo / src_dir)], check=True)
        for file in ["pyproject.toml", "README.md", "LICENSE"]:
            (repo / file).write_bytes((root / file).read_bytes())

        # Simulate a release checkout at a tag.
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "release source"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "tag", "v9.9.9"], cwd=repo, check=True, capture_output=True)

        dist = repo / "dist"
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        wheels = list(dist.glob("*.whl"))
        assert len(wheels) == 1, f"expected one wheel, got {wheels}"
        assert _wheel_version(wheels[0]) == "9.9.9", (
            f"wheel version should equal the release tag, got {_wheel_version(wheels[0])!r}"
        )


def test_wheel_version_without_tag_uses_fallback():
    """A source tree without VCS metadata uses the configured fallback version."""
    pytest.importorskip("build")

    root = _repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()

        # Copy project files but deliberately leave .git behind so VCS detection
        # falls back to the configured fallback version. This mirrors building from
        # an sdist or a checkout with no VCS metadata.
        for src_dir in ["src", "skills", "instructions"]:
            if (root / src_dir).exists():
                shutil.copytree(
                    root / src_dir,
                    repo / src_dir,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
        for file in ["pyproject.toml", "README.md", "LICENSE"]:
            (repo / file).write_bytes((root / file).read_bytes())

        assert not (repo / ".git").exists(), "repo should not be a git checkout"

        dist = repo / "dist"
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        wheels = list(dist.glob("*.whl"))
        assert len(wheels) == 1, f"expected one wheel, got {wheels}"
        pyproject = tomllib.loads(root.joinpath("pyproject.toml").read_text())
        fallback = pyproject["tool"]["hatch"]["version"]["fallback-version"]
        assert _wheel_version(wheels[0]) == fallback, (
            f"wheel version should equal fallback version {fallback!r}, "
            f"got {_wheel_version(wheels[0])!r}"
        )
