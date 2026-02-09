"""Install/uninstall lgrep into OpenCode as a custom tool + MCP server + skill.

Usage:
    lgrep install-opencode
    lgrep uninstall-opencode

This writes three things:
1. Custom tool: ~/.config/opencode/tools/lgrep.ts  (thin CLI wrapper)
2. MCP entry:   ~/.config/opencode/opencode.json    (streamable-http server)
3. Skill:       ~/.config/opencode/skills/lgrep/SKILL.md  (decision matrix)

The custom tool is a pass-through to `lgrep search` CLI.  MCP stays canonical;
the tool is a convenience that gives OpenCode a native tool definition with
proper Zod schema and descriptions.
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

# The SKILL.md lives in the lgrep package itself
_PACKAGE_SKILL = Path(__file__).resolve().parent.parent.parent / "skills" / "lgrep" / "SKILL.md"


def _config_path() -> Path:
    """Resolve the OpenCode config file path (json or jsonc)."""
    json_path = OPENCODE_CONFIG_DIR / "opencode.json"
    jsonc_path = OPENCODE_CONFIG_DIR / "opencode.jsonc"
    if json_path.exists():
        return json_path
    if jsonc_path.exists():
        return jsonc_path
    return json_path  # default to .json


# ---------------------------------------------------------------------------
# Custom tool template — thin wrapper around `lgrep search` CLI
# ---------------------------------------------------------------------------

TOOL_TEMPLATE = r"""import { tool } from "@opencode-ai/plugin"

export default tool({
  description:
    "Semantic code search using Voyage Code 3 embeddings. " +
    "Returns file paths, line ranges, and code snippets ranked by relevance. " +
    "Use natural language queries — understands code meaning, not just text patterns.",
  args: {
    q: tool.schema.string().describe("Natural language search query"),
    path: tool.schema
      .string()
      .describe("Absolute path to the project to search"),
    m: tool.schema
      .number()
      .default(10)
      .describe("Maximum number of results (default: 10)"),
  },
  async execute(args, context) {
    const projectPath = args.path || context.worktree || context.directory
    const result = await Bun.$`lgrep search ${args.q} ${projectPath} -m ${args.m}`.text()
    return result.trim()
  },
})
"""


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install() -> int:
    """Install lgrep into OpenCode (tool + MCP + skill)."""
    print("Installing lgrep into OpenCode...")

    # 1. Write custom tool
    TOOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOOL_PATH.write_text(TOOL_TEMPLATE)
    print(f"  [ok] Custom tool written to {TOOL_PATH}")

    # 2. Copy SKILL.md
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    if _PACKAGE_SKILL.exists():
        shutil.copy2(_PACKAGE_SKILL, SKILL_PATH)
        print(f"  [ok] Skill copied to {SKILL_PATH}")
    else:
        print(f"  [warn] Skill source not found at {_PACKAGE_SKILL}, skipping")

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
