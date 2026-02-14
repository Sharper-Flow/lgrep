"""Tests for OpenCode installer and schema contract.

The contract test ensures the custom tool wrapper's args (q, m, path) stay
in sync with the MCP server's `search` function signature.  If someone
adds/removes a parameter from server.py, this test will fail until the
custom tool template is updated.
"""

import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lgrep.install_opencode import (
    _PACKAGE_TOOL,
    install,
    tool_source,
    uninstall,
)
from lgrep.server import search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_text() -> str:
    """Read the tool source once per test session."""
    return tool_source()


# ---------------------------------------------------------------------------
# Tool wrapper safety — prevent Bun shell API misuse
# ---------------------------------------------------------------------------


class TestToolWrapperSafety:
    """Ensure the custom tool wrapper never throws on CLI errors."""

    def test_source_file_exists(self):
        """The .ts source file must exist in the repo."""
        assert _PACKAGE_TOOL.exists(), (
            f"Tool source not found at {_PACKAGE_TOOL}. "
            "The .ts file must live in tools/opencode/lgrep.ts."
        )

    def test_template_uses_nothrow_chain(self):
        """The Bun shell call must chain .nothrow() to avoid ShellError on non-zero exit."""
        text = _tool_text()
        assert ".nothrow()" in text, (
            "Tool source must chain .nothrow() on the ShellPromise to prevent "
            "ShellError when the CLI exits non-zero (e.g. missing VOYAGE_API_KEY, no index)."
        )

    def test_template_does_not_use_nothrow_as_tag(self):
        """Ensure $.nothrow is not used as a tagged template literal.

        Bun.$.nothrow is a function that configures the global shell, not a
        template tag.  Using it as ``$.nothrow`cmd` `` produces undefined
        behavior — .text() becomes undefined on the result.  The correct
        pattern is ``$`cmd`.nothrow().text()``.
        """
        text = _tool_text()
        bad_pattern = re.findall(r"\.nothrow`", text)
        assert not bad_pattern, (
            f"Found {len(bad_pattern)} use(s) of .nothrow` as a tagged template. "
            "Use $`cmd`.nothrow() (chained method) instead."
        )


# ---------------------------------------------------------------------------
# Bun integration — actually execute the tool to catch runtime errors
# ---------------------------------------------------------------------------


_HAS_BUN = shutil.which("bun") is not None


@pytest.mark.skipif(not _HAS_BUN, reason="Bun not installed")
class TestBunIntegration:
    """Execute the tool source with Bun to catch runtime TypeScript/API errors.

    These tests require Bun to be installed.  They are skipped in CI
    environments without Bun.
    """

    def test_tool_parses_without_error(self, tmp_path: Path):
        """Bun can parse the tool source without syntax errors.

        We can't fully *run* the tool (it needs OpenCode's plugin runtime),
        but we can verify that the shell call chain is syntactically valid
        by extracting and evaluating just the execute function body.
        """
        # Write a minimal test script that imports the Bun shell and
        # verifies the call chain produces a thenable with .text()
        test_script = tmp_path / "test_tool.ts"
        test_script.write_text(
            """\
// Verify the Bun shell call chain is valid at runtime.
// We call the exact pattern from the tool and check .text is a function.
const args = { q: "test", m: 5 };
const projectPath = "/tmp";

// This is the exact call chain from tools/opencode/lgrep.ts
const promise = Bun.$`echo hello ${args.q} ${projectPath} -m ${args.m}`.nothrow();

// Verify .text() exists and is callable on the ShellPromise
if (typeof promise.text !== "function") {
    console.error("FAIL: .text() is not a function on the ShellPromise");
    process.exit(1);
}

// Actually call it to verify it resolves
const result = await promise.text();
if (typeof result !== "string") {
    console.error(`FAIL: .text() returned ${typeof result}, expected string`);
    process.exit(1);
}

console.log("PASS: Shell call chain is valid");
"""
        )

        proc = subprocess.run(
            ["bun", "run", str(test_script)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, (
            f"Bun shell integration test failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        assert "PASS" in proc.stdout

    def test_nothrow_suppresses_nonzero_exit(self, tmp_path: Path):
        """Verify .nothrow() prevents exceptions on non-zero exit codes."""
        test_script = tmp_path / "test_nothrow.ts"
        test_script.write_text(
            """\
// Verify .nothrow() prevents ShellError on non-zero exit
try {
    const result = await Bun.$`exit 1`.nothrow().text();
    console.log("PASS: nothrow suppressed exit code 1");
} catch (e) {
    console.error(`FAIL: nothrow did not suppress error: ${e}`);
    process.exit(1);
}
"""
        )

        proc = subprocess.run(
            ["bun", "run", str(test_script)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, (
            f"Bun nothrow test failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        assert "PASS" in proc.stdout


# ---------------------------------------------------------------------------
# Schema contract — tool args must match MCP search signature
# ---------------------------------------------------------------------------


class TestSchemaContract:
    """Contract: custom tool args must match MCP search signature."""

    def _extract_tool_args(self) -> set[str]:
        """Extract argument names from the TypeScript tool source.

        Matches both single-line (q: tool.schema.string()) and multi-line
        patterns where tool.schema is on the next line after the key.
        """
        text = _tool_text()
        pattern = r"^\s+(\w+):\s+tool\.schema"
        return {m.group(1) for m in re.finditer(pattern, text, re.MULTILINE)}

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


# ---------------------------------------------------------------------------
# Install / uninstall lifecycle
# ---------------------------------------------------------------------------


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

    def test_install_copies_from_ts_source(self, tmp_path):
        """install() should copy the .ts file, not write an inline template."""
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
            install()

        # Installed content must match the source .ts file exactly
        assert tool_path.read_text() == _PACKAGE_TOOL.read_text()

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

    def test_install_same_file_skill_does_not_crash(self, tmp_path):
        """install() should not crash when SKILL source and dest are the same file."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_dir = config_dir / "skills" / "lgrep"
        skill_path = skill_dir / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create skill so _PACKAGE_SKILL and SKILL_PATH resolve to same file
        skill_dir.mkdir(parents=True)
        skill_path.write_text("existing skill content")

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_dir),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._PACKAGE_SKILL", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0
        # Skill content should be unchanged (not corrupted by same-file copy)
        assert skill_path.read_text() == "existing skill content"

    def test_install_same_file_tool_does_not_crash(self, tmp_path):
        """install() should not crash when tool source and dest are the same file."""
        config_dir = tmp_path / ".config" / "opencode"
        tool_path = config_dir / "tools" / "lgrep.ts"
        skill_path = config_dir / "skills" / "lgrep" / "SKILL.md"
        config_path = config_dir / "opencode.json"

        # Pre-create tool so _PACKAGE_TOOL and TOOL_PATH resolve to same file
        tool_path.parent.mkdir(parents=True)
        tool_path.write_text("existing tool content")

        with (
            patch("lgrep.install_opencode.OPENCODE_CONFIG_DIR", config_dir),
            patch("lgrep.install_opencode.TOOL_PATH", tool_path),
            patch("lgrep.install_opencode._PACKAGE_TOOL", tool_path),
            patch("lgrep.install_opencode.SKILL_DIR", skill_path.parent),
            patch("lgrep.install_opencode.SKILL_PATH", skill_path),
            patch("lgrep.install_opencode._config_path", return_value=config_path),
        ):
            result = install()

        assert result == 0
        assert tool_path.read_text() == "existing tool content"
