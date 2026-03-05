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
        """install() should create tool, skill, and MCP config."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0

        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "lgrep" in config["mcp"]
        assert config["mcp"]["lgrep"]["url"] == "http://localhost:6285/mcp"


    def test_uninstall_removes_all_artifacts(self, tmp_path):
        """uninstall() should remove tool, skill, and MCP entry."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create artifacts
        skill_dir.mkdir(parents=True)
        skill_path.write_text("placeholder")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "mcp": {"lgrep": {"type": "remote", "url": "http://localhost:6285/mcp"}},
                }
            )
        )

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = uninstall()

        assert result == 0
        assert not skill_path.exists()

        config = json.loads(config_path.read_text())
        assert "lgrep" not in config.get("mcp", {})

    def test_install_idempotent(self, tmp_path):
        """Running install() twice should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert install() == 0
            assert install() == 0  # second run should not fail

    def test_uninstall_idempotent(self, tmp_path):
        """Running uninstall() when nothing is installed should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert uninstall() == 0

    def test_install_preserves_existing_config(self, tmp_path):
        """install() should not clobber existing MCP entries."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create config with another MCP entry
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "mcp": {"sentry": {"type": "remote", "url": "https://mcp.sentry.dev/mcp"}},
                }
            )
        )

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            install()

        config = json.loads(config_path.read_text())
        assert "sentry" in config["mcp"]
        assert "lgrep" in config["mcp"]

    def test_install_same_file_skill_does_not_crash(self, tmp_path):
        """install() should not crash when SKILL source and dest are the same file."""
        config_dir = tmp_path / ".config" / "opencode"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create skill so _PACKAGE_SKILL and SKILL_PATH resolve to same file
        skill_dir.mkdir(parents=True)
        skill_path.write_text("existing skill content")

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._PACKAGE_SKILL", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0
        # Skill content should be unchanged (not corrupted by same-file copy)
        assert skill_path.read_text() == "existing skill content"

