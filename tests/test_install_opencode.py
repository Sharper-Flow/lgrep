"""Tests for OpenCode installer and schema contract.

The contract test ensures the custom tool wrapper's args (q, m, path) stay
in sync with the MCP server's `search` function signature.  If someone
adds/removes a parameter from server.py, this test will fail until the
custom tool template is updated.
"""

import inspect
import json
import re
from unittest.mock import patch

from lgrep.install_opencode import (
    TOOL_TEMPLATE,
    install,
    uninstall,
)
from lgrep.server import search


class TestSchemaContract:
    """Contract: custom tool args must match MCP search signature."""

    def _extract_tool_args(self) -> set[str]:
        """Extract argument names from the TypeScript tool template.

        Matches both single-line (q: tool.schema.string()) and multi-line
        patterns where tool.schema is on the next line after the key.
        """
        # Match:  key: tool.schema.  (same line)
        # or:     key: tool.schema\n  .  (next line, via .string() continuation)
        pattern = r"^\s+(\w+):\s+tool\.schema"
        return {m.group(1) for m in re.finditer(pattern, TOOL_TEMPLATE, re.MULTILINE)}

    def _get_mcp_search_params(self) -> set[str]:
        """Get parameter names from the MCP search function, excluding internals."""
        sig = inspect.signature(search)
        # Exclude ctx (internal MCP context) and hybrid (advanced, not in wrapper)
        internal = {"ctx", "hybrid"}
        return {name for name in sig.parameters if name not in internal}

    def test_tool_args_subset_of_mcp_params(self):
        """Every custom tool arg must exist in the MCP search function."""
        tool_args = self._extract_tool_args()
        mcp_params = self._get_mcp_search_params()

        missing = tool_args - mcp_params
        assert not missing, (
            f"Custom tool has args not in MCP search: {missing}. "
            f"Tool args: {tool_args}, MCP params: {mcp_params}"
        )

    def test_required_tool_args_present(self):
        """The custom tool must expose at least q, m, and path."""
        tool_args = self._extract_tool_args()
        required = {"q", "m", "path"}
        missing = required - tool_args
        assert not missing, f"Custom tool missing required args: {missing}"

    def test_mcp_search_accepts_q_alias(self):
        """MCP search must accept 'q' as alias for 'query'."""
        sig = inspect.signature(search)
        assert "q" in sig.parameters, "MCP search must accept 'q' alias"

    def test_mcp_search_accepts_m_alias(self):
        """MCP search must accept 'm' as alias for 'limit'."""
        sig = inspect.signature(search)
        assert "m" in sig.parameters, "MCP search must accept 'm' alias"


class TestInstallUninstall:
    """Tests for install/uninstall lifecycle."""

    def test_install_creates_all_artifacts(self, tmp_path):
        """install() should create tool, skill, and MCP config."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0
        assert tool_path.exists()
        assert "tool.schema.string()" in tool_path.read_text()

        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "lgrep" in config["mcp"]
        assert config["mcp"]["lgrep"]["url"] == "http://localhost:6285/mcp"

    def test_uninstall_removes_all_artifacts(self, tmp_path):
        """uninstall() should remove tool, skill, and MCP entry."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create artifacts
        tool_path.parent.mkdir(parents=True)
        tool_path.write_text("placeholder")
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
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = uninstall()

        assert result == 0
        assert not tool_path.exists()
        assert not skill_path.exists()

        config = json.loads(config_path.read_text())
        assert "lgrep" not in config.get("mcp", {})

    def test_install_idempotent(self, tmp_path):
        """Running install() twice should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert install() == 0
            assert install() == 0  # second run should not fail

    def test_uninstall_idempotent(self, tmp_path):
        """Running uninstall() when nothing is installed should not error."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            assert uninstall() == 0

    def test_install_preserves_existing_config(self, tmp_path):
        """install() should not clobber existing MCP entries."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
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
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            install()

        config = json.loads(config_path.read_text())
        assert "sentry" in config["mcp"]
        assert "lgrep" in config["mcp"]
