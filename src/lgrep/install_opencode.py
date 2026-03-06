"""Install/uninstall lgrep into OpenCode as an MCP server + instruction + skill.

Usage:
    lgrep install-opencode
    lgrep uninstall-opencode

This writes three things:
1. MCP entry:       ~/.config/opencode/opencode.json    (remote/HTTP server)
2. Instruction:     ~/.config/opencode/instructions/lgrep-tools.md  (always-on policy)
3. Skill:           ~/.config/opencode/skills/lgrep/SKILL.md  (reference docs)

lgrep runs as a single shared HTTP server (--transport streamable-http).
One process serves all OpenCode sessions simultaneously — opening 5 sessions
does not spawn 5 lgrep processes.  The installer configures OpenCode to connect
via HTTP and prints instructions for running lgrep as a persistent daemon.

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
INSTRUCTION_DIR = OPENCODE_CONFIG_DIR / "instructions"
INSTRUCTION_PATH = INSTRUCTION_DIR / "lgrep-tools.md"
SKILL_DIR = OPENCODE_CONFIG_DIR / "skills" / "lgrep"
SKILL_PATH = SKILL_DIR / "SKILL.md"

# Source assets live at the repo root (alongside src/, skills/, tools/)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PACKAGE_INSTRUCTION = _REPO_ROOT / "instructions" / "lgrep-tools.md"
_PACKAGE_SKILL = _REPO_ROOT / "skills" / "lgrep" / "SKILL.md"


def _config_path() -> Path:
    """Resolve the OpenCode config file path (json or jsonc)."""
    json_path = OPENCODE_CONFIG_DIR / "opencode.json"
    jsonc_path = OPENCODE_CONFIG_DIR / "opencode.jsonc"
    if json_path.exists():
        return json_path
    if jsonc_path.exists():
        return jsonc_path
    return json_path  # default to .json


def _check_instructions_have_lgrep_policy(instructions: list[str]) -> bool:
    """Check if any always-loaded instruction file contains lgrep routing policy.

    Reads each instruction file referenced in the config and checks for the
    lgrep first-action policy marker. Returns True if at least one file
    contains lgrep routing guidance.
    """
    for instruction_path_str in instructions:
        try:
            instruction_path = Path(instruction_path_str).expanduser()
            if instruction_path.exists():
                content = instruction_path.read_text(encoding="utf-8").lower()
                # Check for the canonical lgrep routing policy markers
                if "lgrep" in content and (
                    "first-action" in content or "first tool" in content or "prefer" in content
                ):
                    return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


# ---------------------------------------------------------------------------
# Daemon setup instructions
# ---------------------------------------------------------------------------

_SYSTEMD_SERVICE = """\
[Unit]
Description=lgrep MCP server (semantic code search)
After=network.target

[Service]
Type=simple
ExecStart={lgrep_bin} --transport streamable-http --port 6285
Restart=on-failure
RestartSec=5
Environment=VOYAGE_API_KEY={api_key_placeholder}
Environment=LGREP_WARM_PATHS={warm_paths_placeholder}
Environment=LGREP_AUTO_WATCH=true
StandardOutput=append:/tmp/lgrep.log
StandardError=append:/tmp/lgrep.log

[Install]
WantedBy=default.target
"""


def _print_daemon_instructions() -> None:
    """Print post-install instructions for running lgrep as a persistent daemon."""
    import shutil

    lgrep_bin = shutil.which("lgrep") or "lgrep"
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = service_dir / "lgrep.service"

    print()
    print("Done!")
    print()
    print("lgrep connects to OpenCode as a shared HTTP server — one process")
    print("serves all sessions simultaneously (no per-session RAM overhead).")
    print()
    print("─── Option A: systemd user service (recommended) ───────────────────")
    print()
    print(f"  mkdir -p {service_dir}")
    print(f"  cat > {service_path} << 'EOF'")
    print(
        _SYSTEMD_SERVICE.format(
            lgrep_bin=lgrep_bin,
            api_key_placeholder="your-voyage-api-key-here",
            warm_paths_placeholder="/path/to/project-a:/path/to/project-b",
        ).rstrip()
    )
    print("EOF")
    print()
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now lgrep.service")
    print()
    print("─── Option B: run manually ─────────────────────────────────────────")
    print()
    print("  VOYAGE_API_KEY=your-key \\")
    print("  LGREP_WARM_PATHS=/path/to/project \\")
    print("  LGREP_AUTO_WATCH=true \\")
    print(f"  {lgrep_bin} --transport streamable-http --port 6285")
    print()
    print("Then open OpenCode — the agent will discover lgrep automatically.")


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install() -> int:
    """Install lgrep into OpenCode (MCP + instruction + skill)."""
    print("Installing lgrep into OpenCode...")

    # 1. Copy always-loaded instruction file
    INSTRUCTION_DIR.mkdir(parents=True, exist_ok=True)
    if not _PACKAGE_INSTRUCTION.exists():
        print(f"  [warn] Instruction source not found at {_PACKAGE_INSTRUCTION}, skipping")
    elif _PACKAGE_INSTRUCTION.resolve() == INSTRUCTION_PATH.resolve():
        print(f"  [ok] Instruction already at {INSTRUCTION_PATH} (same file)")
    else:
        shutil.copy2(_PACKAGE_INSTRUCTION, INSTRUCTION_PATH)
        print(f"  [ok] Instruction copied to {INSTRUCTION_PATH}")

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

    if "instructions" not in config:
        config["instructions"] = []

    config["mcp"]["lgrep"] = {
        "type": "remote",
        "url": "http://localhost:6285/mcp",
        "enabled": True,
    }

    instruction_entry = "~/.config/opencode/instructions/lgrep-tools.md"
    if instruction_entry not in config["instructions"]:
        config["instructions"].append(instruction_entry)

    # 4. Verify lgrep routing policy is in always-loaded instructions
    instructions = config.get("instructions", [])
    has_lgrep_policy = _check_instructions_have_lgrep_policy(instructions)

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  [ok] MCP entry added to {config_path}")

    if not has_lgrep_policy:
        print()
        print("  [warn] No always-loaded instruction file references lgrep routing policy.")
        print("         Agents will see lgrep tools but may not prefer them over grep/glob.")
        print(f"         Check the 'instructions' array in {config_path} and ensure it includes")
        print("         ~/.config/opencode/instructions/lgrep-tools.md")
    else:
        print("  [ok] lgrep routing policy found in always-loaded instructions")

    _print_daemon_instructions()

    return 0


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall() -> int:
    """Remove lgrep from OpenCode (tool + MCP + skill)."""
    print("Uninstalling lgrep from OpenCode...")

    # 1. Remove installed instruction
    if INSTRUCTION_PATH.exists():
        INSTRUCTION_PATH.unlink()
        print(f"  [ok] Removed {INSTRUCTION_PATH}")
    else:
        print(f"  [skip] {INSTRUCTION_PATH} not found")

    # 2. Remove skill
    if SKILL_PATH.exists():
        SKILL_PATH.unlink()
        # Remove empty directory
        if SKILL_DIR.exists() and not any(SKILL_DIR.iterdir()):
            SKILL_DIR.rmdir()
        print(f"  [ok] Removed {SKILL_PATH}")
    else:
        print(f"  [skip] {SKILL_PATH} not found")

    # 3. Remove MCP entry and installed instruction entry from opencode.json
    config_path = _config_path()
    if config_path.exists():
        config = json.loads(config_path.read_text())
        if "mcp" in config and "lgrep" in config["mcp"]:
            del config["mcp"]["lgrep"]
        else:
            print(f"  [skip] No lgrep MCP entry in {config_path}")

        instruction_entry = "~/.config/opencode/instructions/lgrep-tools.md"
        if "instructions" in config and instruction_entry in config["instructions"]:
            config["instructions"] = [i for i in config["instructions"] if i != instruction_entry]
            if not config["instructions"]:
                del config["instructions"]
            print(f"  [ok] Removed instruction entry from {config_path}")
        else:
            print(f"  [skip] No lgrep instruction entry in {config_path}")

        config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"  [ok] Config updated at {config_path}")
    else:
        print(f"  [skip] {config_path} not found")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        sys.exit(uninstall())
    sys.exit(install())
