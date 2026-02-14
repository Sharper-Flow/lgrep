"""Install/uninstall lgrep into OpenCode as a custom tool + MCP server + skill.

Usage:
    lgrep install-opencode
    lgrep uninstall-opencode

This writes three things:
1. Custom tool: ~/.config/opencode/tools/lgrep.ts  (thin CLI wrapper)
2. MCP entry:   ~/.config/opencode/opencode.json    (streamable-http server)
3. Skill:       ~/.config/opencode/skills/lgrep/SKILL.md  (decision matrix)

The custom tool source lives in tools/opencode/lgrep.ts (a real .ts file that
editors and Bun can check).  The installer copies it to the OpenCode config dir.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

OPENCODE_CONFIG_DIR = Path.home() / ".config" / "opencode"
TOOL_PATH = OPENCODE_CONFIG_DIR / "tools" / "lgrep.ts"
SKILL_DIR = OPENCODE_CONFIG_DIR / "skills" / "lgrep"
SKILL_PATH = SKILL_DIR / "SKILL.md"

# Source assets live at the repo root (alongside src/, skills/, tools/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PACKAGE_SKILL = _REPO_ROOT / "skills" / "lgrep" / "SKILL.md"
_PACKAGE_TOOL = _REPO_ROOT / "tools" / "opencode" / "lgrep.ts"


def _config_path() -> Path:
    """Resolve the OpenCode config file path (json or jsonc)."""
    json_path = OPENCODE_CONFIG_DIR / "opencode.json"
    jsonc_path = OPENCODE_CONFIG_DIR / "opencode.jsonc"
    if json_path.exists():
        return json_path
    if jsonc_path.exists():
        return jsonc_path
    return json_path  # default to .json


def tool_source() -> str:
    """Read the custom tool TypeScript source from the repo.

    Returns the contents of tools/opencode/lgrep.ts.  This is the single
    source of truth — no inline string template.
    """
    if not _PACKAGE_TOOL.exists():
        raise FileNotFoundError(
            f"Tool source not found at {_PACKAGE_TOOL}. "
            "Are you running from a proper lgrep checkout?"
        )
    return _PACKAGE_TOOL.read_text()


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install() -> int:
    """Install lgrep into OpenCode (tool + MCP + skill)."""
    print("Installing lgrep into OpenCode...")

    # 1. Copy custom tool from repo source
    TOOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _PACKAGE_TOOL.resolve() == TOOL_PATH.resolve():
        print(f"  [ok] Tool already at {TOOL_PATH} (same file)")
    elif not _PACKAGE_TOOL.exists():
        print(f"  [warn] Tool source not found at {_PACKAGE_TOOL}, skipping")
    else:
        shutil.copy2(_PACKAGE_TOOL, TOOL_PATH)
        print(f"  [ok] Custom tool copied to {TOOL_PATH}")

    # 2. Copy SKILL.md (skip if source and dest resolve to the same file)
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    if not _PACKAGE_SKILL.exists():
        print(f"  [warn] Skill source not found at {_PACKAGE_SKILL}, skipping")
    elif _PACKAGE_SKILL.resolve() == SKILL_PATH.resolve():
        print(f"  [ok] Skill already at {SKILL_PATH} (same file)")
    else:
        shutil.copy2(_PACKAGE_SKILL, SKILL_PATH)
        print(f"  [ok] Skill copied to {SKILL_PATH}")

    # 3. Add MCP entry to opencode.json
    config_path = _config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {"$schema": "https://opencode.ai/config.json"}

    if "mcp" not in config:
        config["mcp"] = {}

    config["mcp"]["lgrep"] = {
        "type": "remote",
        "url": "http://localhost:6285/mcp",
        "enabled": True,
    }

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  [ok] MCP entry added to {config_path}")

    print()
    print("Done. Start the lgrep server with:")
    print("  lgrep --transport streamable-http")
    print()
    print("Then use OpenCode normally — the agent will discover lgrep via")
    print("the skill, MCP tools, and the custom tool automatically.")

    return 0


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall() -> int:
    """Remove lgrep from OpenCode (tool + MCP + skill)."""
    print("Uninstalling lgrep from OpenCode...")

    # 1. Remove custom tool
    if TOOL_PATH.exists():
        TOOL_PATH.unlink()
        print(f"  [ok] Removed {TOOL_PATH}")
    else:
        print(f"  [skip] {TOOL_PATH} not found")

    # 2. Remove skill
    if SKILL_PATH.exists():
        SKILL_PATH.unlink()
        # Remove empty directory
        if SKILL_DIR.exists() and not any(SKILL_DIR.iterdir()):
            SKILL_DIR.rmdir()
        print(f"  [ok] Removed {SKILL_PATH}")
    else:
        print(f"  [skip] {SKILL_PATH} not found")

    # 3. Remove MCP entry from opencode.json
    config_path = _config_path()
    if config_path.exists():
        config = json.loads(config_path.read_text())
        if "mcp" in config and "lgrep" in config["mcp"]:
            del config["mcp"]["lgrep"]
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            print(f"  [ok] MCP entry removed from {config_path}")
        else:
            print(f"  [skip] No lgrep MCP entry in {config_path}")
    else:
        print(f"  [skip] {config_path} not found")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        sys.exit(uninstall())
    sys.exit(install())
