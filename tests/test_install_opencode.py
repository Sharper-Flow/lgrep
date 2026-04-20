"""Tests for OpenCode installer and schema contract.

The contract test ensures the custom tool wrapper's args (q, m, path) stay
in sync with the MCP server's `search` function signature.  If someone
adds/removes a parameter from server.py, this test will fail until the
custom tool template is updated.
"""

import json
from unittest.mock import patch

from lgrep.install_opencode import (
    install,
    uninstall,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Install / uninstall lifecycle
# ---------------------------------------------------------------------------


class TestInstallUninstall:
    """Tests for install/uninstall lifecycle."""

    def test_install_creates_all_artifacts(self, tmp_path):
        """install() should create instruction, skill, and MCP config."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0

        assert config_path.exists()
        assert instruction_path.exists()
        config = json.loads(config_path.read_text())
        assert "lgrep" in config["mcp"]
        assert config["mcp"]["lgrep"]["url"] == "http://localhost:6285/mcp"
        assert "~/.config/opencode/instructions/lgrep-tools.md" in config["instructions"]

    def test_uninstall_removes_all_artifacts(self, tmp_path):
        """uninstall() should remove instruction, skill, and MCP entry."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_dir = config_dir / "instructions"
        instruction_path = instruction_dir / "lgrep-tools.md"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create artifacts
        instruction_dir.mkdir(parents=True)
        instruction_path.write_text("policy")
        skill_dir.mkdir(parents=True)
        skill_path.write_text("placeholder")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "instructions": ["~/.config/opencode/instructions/lgrep-tools.md"],
                    "mcp": {"lgrep": {"type": "remote", "url": "http://localhost:6285/mcp"}},
                }
            )
        )

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_dir),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = uninstall()

        assert result == 0
        assert not instruction_path.exists()
        assert not skill_path.exists()

        config = json.loads(config_path.read_text())
        assert "lgrep" not in config.get("mcp", {})
        assert (
            "instructions" not in config
            or "~/.config/opencode/instructions/lgrep-tools.md"
            not in config.get("instructions", [])
        )

    def test_install_idempotent(self, tmp_path):
        """Running install() twice should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert install() == 0
            assert install() == 0  # second run should not fail

    def test_uninstall_idempotent(self, tmp_path):
        """Running uninstall() when nothing is installed should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert uninstall() == 0

    def test_install_preserves_existing_config(self, tmp_path):
        """install() should not clobber existing MCP entries."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create config with another MCP entry
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "instructions": ["~/.config/opencode/instructions/identity.md"],
                    "mcp": {"sentry": {"type": "remote", "url": "https://mcp.sentry.dev/mcp"}},
                }
            )
        )

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            install()

        config = json.loads(config_path.read_text())
        assert "sentry" in config["mcp"]
        assert "lgrep" in config["mcp"]
        assert "~/.config/opencode/instructions/identity.md" in config["instructions"]
        assert "~/.config/opencode/instructions/lgrep-tools.md" in config["instructions"]

    def test_uninstall_refuses_when_skill_dir_is_symlink_into_package(self, tmp_path):
        """When SKILL_DIR itself is a symlink whose target lives inside the
        installed package tree (common dev-workflow setup:
        ``~/.config/opencode/skills/lgrep -> <repo>/skills/lgrep``),
        ``SKILL_PATH`` resolves through the parent symlink to a real file
        in the package tree. A naive ``SKILL_PATH.unlink()`` destroys that
        real file.

        uninstall() MUST detect this case and skip the unlink (or unlink only
        the dir-level symlink, never its target).

        We build a fake package tree under tmp_path and patch
        ``_PACKAGE_SKILL`` to point at it so the test never risks the
        real repo files during the red phase.
        """
        # Fake package tree under tmp_path
        fake_pkg_skill_dir = tmp_path / "pkg" / "skills" / "lgrep"
        fake_pkg_skill_dir.mkdir(parents=True)
        fake_pkg_skill = fake_pkg_skill_dir / "SKILL.md"
        fake_pkg_skill.write_text("FAKE_PACKAGE_SKILL_SENTINEL")
        before_bytes = fake_pkg_skill.read_bytes()

        # User's OpenCode config has SKILL_DIR as a symlink INTO the fake pkg.
        config_dir = tmp_path / ".config" / "opencode"
        (config_dir / "skills").mkdir(parents=True)
        skill_dir_link = config_dir / "skills" / "lgrep"
        skill_dir_link.symlink_to(fake_pkg_skill_dir)
        skill_path = skill_dir_link / "SKILL.md"

        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        config_path = config_dir / "opencode.json"
        config_path.write_text('{"mcp":{}, "instructions":[]}')

        with (
            patch("lgrep.install_opencode._PACKAGE_SKILL", fake_pkg_skill),
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir_link),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
        ):
            rc = uninstall()

        assert rc == 0
        assert fake_pkg_skill.exists(), \
            "package SKILL.md was unlinked through the parent dir symlink"
        assert fake_pkg_skill.read_bytes() == before_bytes

    def test_uninstall_refuses_when_instruction_dir_is_symlink_into_package(self, tmp_path):
        """Same guard for INSTRUCTION_DIR when a user symlinks the whole
        instructions dir into a source checkout.
        """
        fake_pkg_instruction_dir = tmp_path / "pkg" / "instructions"
        fake_pkg_instruction_dir.mkdir(parents=True)
        fake_pkg_instruction = fake_pkg_instruction_dir / "lgrep-tools.md"
        fake_pkg_instruction.write_text("FAKE_PACKAGE_INSTRUCTION_SENTINEL")
        before_bytes = fake_pkg_instruction.read_bytes()

        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        instruction_dir_link = config_dir / "instructions"
        instruction_dir_link.symlink_to(fake_pkg_instruction_dir)
        instruction_path = instruction_dir_link / "lgrep-tools.md"

        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"
        config_path.write_text('{"mcp":{}, "instructions":[]}')

        with (
            patch("lgrep.install_opencode._PACKAGE_INSTRUCTION", fake_pkg_instruction),
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_dir_link),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
        ):
            rc = uninstall()

        assert rc == 0
        assert fake_pkg_instruction.exists(), \
            "package lgrep-tools.md was unlinked through the parent dir symlink"
        assert fake_pkg_instruction.read_bytes() == before_bytes

    def test_install_same_file_skill_does_not_crash(self, tmp_path):
        """install() should not crash when SKILL source and dest are the same file."""
        config_dir = tmp_path / ".config" / "opencode"
        instruction_path = config_dir / "instructions" / "lgrep-tools.md"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create skill so _PACKAGE_SKILL and SKILL_PATH resolve to same file
        skill_dir.mkdir(parents=True)
        skill_path.write_text("existing skill content")

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.INSTRUCTION_DIR", instruction_path.parent),
            patch("lgrep.install_opencode.INSTRUCTION_PATH", instruction_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._PACKAGE_SKILL", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0
        # Skill content should be unchanged (not corrupted by same-file copy)
        assert skill_path.read_text() == "existing skill content"
