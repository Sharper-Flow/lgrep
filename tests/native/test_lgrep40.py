"""Ignore semantics and security precedence for the native discovery harness."""

from pathlib import Path

import pytest

from lgrep.discovery import FileDiscovery


@pytest.mark.parametrize("ignore_name", [".gitignore", ".lgrepignore"])
class TestIgnoreSemantics:
    def test_anchored_nested_and_ordered_patterns(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text(
            "/root-only.py\ndocs/**/*.json\n*.log\n!keep.log\nlast.log\n!last.log\nlast.log\n"
        )
        expected = {
            "root-only.py": True,
            "nested/root-only.py": False,
            "docs/info.json": True,
            "docs/deep/info.json": True,
            "nested/docs/info.json": False,
            "nested/drop.log": True,
            "nested/keep.log": False,
            "keep.log": False,
            "last.log": True,
            "main.py": False,
        }
        for name in expected:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source\n")

        discovery = FileDiscovery(tmp_path)
        for name, ignored in expected.items():
            assert discovery.is_ignored(name) is ignored, name
            assert discovery.is_ignored(tmp_path / name) is ignored, name
        found = {path.relative_to(tmp_path).as_posix() for path in discovery.find_files()}
        assert found == {ignore_name} | {name for name, ignored in expected.items() if not ignored}

    def test_directory_only_pattern_distinguishes_files(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("generated/\n")
        generated = tmp_path / "generated"
        generated.mkdir()
        (generated / "hidden.py").write_text("source\n")
        nested = tmp_path / "nested"
        nested.mkdir()
        same_named_file = nested / "generated"
        same_named_file.write_text("source\n")

        discovery = FileDiscovery(tmp_path)
        assert discovery.is_ignored(generated) is True
        assert discovery.is_ignored(generated / "hidden.py") is True
        assert discovery.is_ignored(same_named_file) is False
        assert same_named_file in set(discovery.find_files())

    def test_ignored_directory_is_pruned(self, tmp_path, ignore_name, monkeypatch):
        import os

        (tmp_path / ignore_name).write_text("generated/\n")
        generated = tmp_path / "generated"
        generated.mkdir()
        (generated / "hidden.py").write_text("source\n")
        (tmp_path / "main.py").write_text("source\n")
        scanned = set()
        scandir = os.scandir

        def record_scandir(path):
            scanned.add(Path(path))
            return scandir(path)

        monkeypatch.setattr(os, "scandir", record_scandir)
        found = set(FileDiscovery(tmp_path).find_files())

        assert generated not in scanned
        assert found == {tmp_path / ignore_name, tmp_path / "main.py"}

    def test_nested_ignore_files_are_not_loaded(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("root-excluded.py\n")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / ignore_name).write_text("main.py\n")
        source = nested / "main.py"
        source.write_text("source\n")

        discovery = FileDiscovery(tmp_path)
        assert discovery.is_ignored(source) is False
        assert source in set(discovery.find_files())

    def test_negation_does_not_reopen_pruned_parent(self, tmp_path, ignore_name):
        (tmp_path / ignore_name).write_text("generated/\n!generated/keep.py\n")
        generated = tmp_path / "generated"
        generated.mkdir()
        kept = generated / "keep.py"
        kept.write_text("source\n")

        discovery = FileDiscovery(tmp_path)
        assert discovery.is_ignored(generated) is True
        assert discovery.is_ignored(kept) is False
        assert kept not in set(discovery.find_files())


def test_ignore_files_combine_as_independent_exclusions(tmp_path):
    (tmp_path / ".gitignore").write_text("git-only.py\n!lgrep-only.py\n")
    (tmp_path / ".lgrepignore").write_text("!git-only.py\nlgrep-only.py\n")
    for name in ("git-only.py", "lgrep-only.py", "main.py"):
        (tmp_path / name).write_text("source\n")

    discovery = FileDiscovery(tmp_path)
    assert discovery.is_ignored("git-only.py") is True
    assert discovery.is_ignored("lgrep-only.py") is True
    assert discovery.is_ignored("main.py") is False
    assert {path.name for path in discovery.find_files()} == {
        ".gitignore",
        ".lgrepignore",
        "main.py",
    }


@pytest.mark.parametrize("ignore_name", [".gitignore", ".lgrepignore"])
def test_negation_cannot_admit_secrets_or_outside_paths(tmp_path, ignore_name):
    from lgrep.discovery import FileDiscovery

    root = tmp_path / "project"
    root.mkdir()
    (root / ignore_name).write_text("*\n!*\n!.env\n!server.pem\n!../outside.py\n")
    secret = root / ".env"
    secret.write_text("SECRET=fixture\n")
    key = root / "server.pem"
    key.write_text("fixture key\n")
    safe = root / "main.py"
    safe.write_text("source\n")
    outside = tmp_path / "outside.py"
    outside.write_text("source\n")
    link = root / "link.py"
    link.symlink_to(outside)

    discovery = FileDiscovery(root)
    assert discovery.is_ignored(secret) is True
    assert discovery.is_ignored(key) is True
    assert discovery.is_ignored(outside) is True
    assert discovery.is_ignored("../outside.py") is True
    assert discovery.is_ignored(link) is True
    assert discovery.is_ignored(safe) is False
    found = set(discovery.find_files())
    assert safe in found
    assert found.isdisjoint({secret, key, outside, link})
