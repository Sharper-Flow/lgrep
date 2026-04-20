"""Tests for install_opencode.py JSONC config handling, log paths, and stdio option.

These tests verify:
1. install() / uninstall() handle .jsonc config files with // and /* */ comments
2. _SYSTEMD_SERVICE template uses user-scoped log path ($HOME/.cache/lgrep/lgrep.log)
3. _print_daemon_instructions includes Option C for stdio per-session MCP config
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lgrep import install_opencode as inst


class TestJsoncConfigHandling:
    """install() and uninstall() must handle .jsonc files with comments."""

    def test_install_loads_jsonc_with_line_comments(self, tmp_path, monkeypatch):
        """A .jsonc file with // line comments must not raise JSONDecodeError."""
        # Set up a temp config dir with a .jsonc file
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    // This is a line comment\n'
            '    "mcp": {},\n'
            '    "instructions": []\n'
            '}\n'
        )
        # Mock the global OPENCODE_CONFIG_DIR
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)
        # Also patch _REPO_ROOT so instruction/skill copy doesn't fail
        monkeypatch.setattr(inst, "_REPO_ROOT", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_INSTRUCTION", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_SKILL", tmp_path / "nonexistent")

        with patch("shutil.copy2"):
            # Should not raise json.JSONDecodeError
            result = inst.install()
        assert result == 0

    def test_install_loads_jsonc_with_block_comments(self, tmp_path, monkeypatch):
        """A .jsonc file with /* block comments */ must not raise JSONDecodeError."""
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    /* block comment */\n'
            '    "mcp": {},\n'
            '    "instructions": []\n'
            '}\n'
        )
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)
        monkeypatch.setattr(inst, "_REPO_ROOT", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_INSTRUCTION", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_SKILL", tmp_path / "nonexistent")

        with patch("shutil.copy2"):
            result = inst.install()
        assert result == 0

    def test_install_loads_jsonc_with_trailing_comma(self, tmp_path, monkeypatch):
        """A .jsonc file with trailing commas must not raise JSONDecodeError."""
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    "mcp": {},\n'
            '    "instructions": [],\n'
            '}\n'
        )
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)
        monkeypatch.setattr(inst, "_REPO_ROOT", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_INSTRUCTION", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_SKILL", tmp_path / "nonexistent")

        with patch("shutil.copy2"):
            result = inst.install()
        assert result == 0

    def test_install_loads_jsonc_with_url_values(self, tmp_path, monkeypatch):
        """A .jsonc file with URLs (https://) must not treat // as comment start."""
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    "mcp": {\n'
            '        "mytool": {\n'
            '            "url": "https://example.com/api",\n'
            '            "enabled": true\n'
            '        }\n'
            '    },\n'
            '    "instructions": []\n'
            '}\n'
        )
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)
        monkeypatch.setattr(inst, "_REPO_ROOT", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_INSTRUCTION", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_SKILL", tmp_path / "nonexistent")

        with patch("shutil.copy2"):
            result = inst.install()
        assert result == 0
        # Verify the URL was preserved in the written config
        content = json.loads(jsonc_file.read_text())
        assert content["mcp"]["mytool"]["url"] == "https://example.com/api"

    def test_uninstall_loads_jsonc_with_comments(self, tmp_path, monkeypatch):
        """uninstall() must also handle .jsonc files."""
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    // pre-existing comment\n'
            '    "mcp": {"lgrep": {"type": "remote"}},\n'
            '    "instructions": ["~/.config/opencode/instructions/lgrep-tools.md"]\n'
            '}\n'
        )
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)

        # Should not raise
        result = inst.uninstall()
        assert result == 0

    def test_install_writes_jsonc_preserving_format(self, tmp_path, monkeypatch):
        """install() must preserve the existing .jsonc file's comment-free content."""
        config_dir = tmp_path / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        jsonc_file = config_dir / "opencode.jsonc"
        jsonc_file.write_text(
            '{\n'
            '    "existing_key": "existing_value",\n'
            '}\n'
        )
        monkeypatch.setattr(inst, "OPENCODE_CONFIG_DIR", config_dir)
        monkeypatch.setattr(inst, "_REPO_ROOT", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_INSTRUCTION", tmp_path / "nonexistent")
        monkeypatch.setattr(inst, "_PACKAGE_SKILL", tmp_path / "nonexistent")

        with patch("shutil.copy2"):
            inst.install()

        # Content should still be valid JSON (comments stripped, structure intact)
        content = json.loads(jsonc_file.read_text())
        assert content["existing_key"] == "existing_value"
        assert "mcp" in content
        assert content["mcp"]["lgrep"]["type"] == "remote"


class TestSystemdLogPath:
    """The systemd service template must use a user-scoped log path."""

    def test_systemd_service_uses_user_cache_log_path(self):
        """StandardOutput and StandardError must use $HOME/.cache/lgrep/lgrep.log."""
        assert "$HOME/.cache/lgrep/lgrep.log" in inst._SYSTEMD_SERVICE
        assert "/tmp/lgrep.log" not in inst._SYSTEMD_SERVICE

    def test_daemon_instructions_mention_cache_dir_creation(self, capsys):
        """_print_daemon_instructions must tell users to mkdir -p ~/.cache/lgrep."""
        inst._print_daemon_instructions()
        output = capsys.readouterr().out
        assert ".cache/lgrep" in output or "~/.cache/lgrep" in output


class TestStdioOption:
    """Option C: stdio per-session must be documented in daemon instructions."""

    def test_print_daemon_instructions_includes_stdio_option(self, capsys):
        """Output must include a stdio (Option C / local) MCP configuration block."""
        inst._print_daemon_instructions()
        output = capsys.readouterr().out
        # Must mention stdio / Option C / type: local
        assert any(
            keyword in output.lower()
            for keyword in ("option c", "stdio", "type: local", "local default")
        )
