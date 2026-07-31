"""Safe trunk-only deployment of a tagged lgrep release to local Vision.

This script is repository maintenance tooling, not runtime package code. It must
only run from a clean checkout of the default branch (``main``), never from a
worktree or feature branch. It downloads the GitHub Release wheel for the
selected tag, installs it into the local uv tool runtime used by Vision,
restarts the Vision user service, and verifies the deployment with real,
non-destructive MCP calls.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_VISION_CONFIG = Path.home() / ".config" / "vision" / "servers.yaml"
LGREP_SERVER_NAME = "lgrep"
RELEASE_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return the completed process."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=True,
        timeout=timeout,
        env=env,
    )


def _git_stdout(
    args: list[str],
    cwd: Path | str | None = None,
) -> str:
    """Run a git command and return stripped stdout, raising on failure."""
    command = ["git", *args]
    if cwd is not None:
        command.insert(1, "-C")
        command.insert(2, str(cwd))
    result = _run(command, check=False, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _is_worktree(cwd: Path | str | None = None) -> bool:
    """Return True when the current checkout is a git worktree."""
    git_dir = Path(_git_stdout(["rev-parse", "--git-dir"], cwd=cwd))
    common_dir = Path(_git_stdout(["rev-parse", "--git-common-dir"], cwd=cwd))
    if not git_dir.is_absolute():
        base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        git_dir = base / git_dir
    if not common_dir.is_absolute():
        base = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        common_dir = base / common_dir
    return git_dir.resolve() != common_dir.resolve()


def check_trunk_only_context(
    cwd: Path | str | None = None,
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether deployment context is safe.

    Deployment is allowed only from a clean, non-worktree checkout of ``main``.
    """
    try:
        inside = _git_stdout(["rev-parse", "--is-inside-work-tree"], cwd=cwd).lower()
    except RuntimeError as exc:
        return False, str(exc)
    if inside != "true":
        return False, "not inside a git work tree"

    branch = _git_stdout(["branch", "--show-current"], cwd=cwd)
    if branch != "main":
        return False, f"branch is {branch!r}, expected 'main'"

    dirty = _git_stdout(["status", "--porcelain"], cwd=cwd)
    if dirty:
        return False, "working tree has uncommitted changes"

    if _is_worktree(cwd=cwd):
        return False, "deploy refused from git worktree"

    return True, ""


def resolve_release_tag(
    explicit_tag: str | None,
    cwd: Path | str | None = None,
) -> str:
    """Resolve and validate a release tag like ``vX.Y.Z``."""
    tag = explicit_tag
    if tag is None:
        try:
            tag = _git_stdout(["describe", "--tags", "--exact-match"], cwd=cwd)
        except RuntimeError as exc:
            raise RuntimeError(f"could not determine release tag at HEAD: {exc}") from exc

    if not RELEASE_TAG_RE.match(tag):
        raise RuntimeError(f"tag {tag!r} does not match vX.Y.Z")
    return tag


def version_from_tag(tag: str) -> str:
    """Strip the leading 'v' from a release tag."""
    return tag.removeprefix("v")


def github_wheel_url(tag: str, repo: str) -> str:
    """Return the GitHub Release wheel URL for the tag and repository."""
    version = version_from_tag(tag)
    owner, name = repo.split("/", 1)
    return (
        f"https://github.com/{quote(owner)}/{quote(name)}/releases/download/"
        f"{quote(tag)}/{quote(name)}-{quote(version)}-py3-none-any.whl"
    )


def download_wheel(url: str, dest: Path) -> None:
    """Download the release wheel to ``dest``."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "lgrep-deploy-vision/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response, dest.open("wb") as handle:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def validate_vision_config(config_path: Path) -> None:
    """Run ``vision config validate``."""
    _run(
        ["vision", "config", "validate", "--config", str(config_path)],
        check=True,
        timeout=30,
    )


def install_wheel(wheel_path: Path, uv_tool_bin_dir: str, uv_tool_dir: str) -> None:
    """Install the wheel into the pinned uv tool runtime.

    Both ``UV_TOOL_BIN_DIR`` and ``UV_TOOL_DIR`` are set explicitly in the
    child environment so an inherited OpenCode per-project uv runtime cannot
    redirect the install target.
    """
    env = os.environ.copy()
    env["UV_TOOL_BIN_DIR"] = uv_tool_bin_dir
    env["UV_TOOL_DIR"] = uv_tool_dir
    _run(
        ["uv", "tool", "install", "--reinstall", str(wheel_path)],
        check=True,
        timeout=300,
        env=env,
    )


def restart_vision_service() -> None:
    """Restart the Vision systemd user service."""
    _run(
        ["systemctl", "--user", "restart", "vision.service"],
        check=True,
        timeout=60,
    )


def vision_health(config_path: Path) -> None:
    """Run ``vision health``."""
    _run(
        ["vision", "health", "--config", str(config_path)],
        check=True,
        timeout=30,
    )


def wait_vision_ready(
    config_path: Path,
    *,
    retries: int = 1,
    delay_seconds: float = 5.0,
) -> None:
    """Wait for Vision health, allowing a bounded number of retries."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            vision_health(config_path)
            return
        except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise RuntimeError(f"Vision did not become healthy after {retries} retries: {last_error}")


def check_installed_version(expected_version: str, command: str) -> None:
    """Assert that the configured lgrep executable reports the expected version."""
    if not command:
        raise RuntimeError("no lgrep command configured")
    result = _run([command, "--version"], check=True, timeout=30)
    prefix = "lgrep "
    if not result.stdout.startswith(prefix):
        raise RuntimeError(f"unexpected version output: {result.stdout!r}")
    installed = result.stdout[len(prefix) :].strip()
    if installed != expected_version:
        raise RuntimeError(
            f"installed lgrep version {installed!r} does not match expected {expected_version!r}"
        )


def _vision_lgrep_server(config_path: Path) -> tuple[int, str]:
    """Parse Vision servers.yaml and return the lgrep server port and command.

    Requires an absolute, executable command path. Rejects missing, non-string,
    relative, or non-executable values so deployment fails before the service is
    restarted.
    """
    # PyYAML is a dev dependency; this script is repository maintenance tooling.
    yaml_spec = importlib.util.find_spec("yaml")
    if yaml_spec is None:
        raise RuntimeError("PyYAML is required to parse Vision config; install dev dependencies")
    import yaml

    data = yaml.safe_load(config_path.read_text())
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise RuntimeError(f"servers map not found in {config_path}")

    server = servers.get(LGREP_SERVER_NAME)
    if server is None:
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} not found in {config_path}")
    if not isinstance(server, dict):
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} is not a map in {config_path}")

    port = server.get("port")
    if not isinstance(port, int):
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} has invalid port {port!r}")

    command = server.get("command")
    if command is None:
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} missing command in {config_path}")
    if not isinstance(command, str):
        raise RuntimeError(
            f"server {LGREP_SERVER_NAME!r} command is not a string ({type(command).__name__})"
        )
    command_path = Path(command)
    if not command_path.is_absolute():
        raise RuntimeError(
            f"server {LGREP_SERVER_NAME!r} command {command!r} is not an absolute path"
        )
    if not command_path.is_file():
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} command {command!r} does not exist")
    if not os.access(command_path, os.X_OK):
        raise RuntimeError(f"server {LGREP_SERVER_NAME!r} command {command!r} is not executable")

    return port, str(command_path)


def _derive_uv_paths_from_command(command: str) -> tuple[str, str]:
    """Derive uv tool directories from a configured launcher shebang.

    Reads the first line of the configured lgrep executable and requires the
    uv launcher shape ``#!<UV_TOOL_DIR>/lgrep/bin/python...``. Returns
    ``(UV_TOOL_BIN_DIR, UV_TOOL_DIR)`` where ``UV_TOOL_BIN_DIR`` is the
    directory containing the launcher and ``UV_TOOL_DIR`` is the parent of the
    ``lgrep`` package directory. Malformed or non-uv launchers raise before
    any restart happens.
    """
    command_path = Path(command)
    try:
        first_line = command_path.read_text().splitlines()[0]
    except (OSError, IndexError) as exc:
        raise RuntimeError(f"configured command {command!r} launcher is unreadable: {exc}") from exc

    shebang = first_line.strip()
    if not shebang.startswith("#!"):
        raise RuntimeError(f"configured command {command!r} is not a shebang launcher")

    interpreter = shebang[2:].strip()
    if not interpreter:
        raise RuntimeError(f"configured command {command!r} has empty shebang interpreter")

    interpreter_path = Path(interpreter)
    parts = interpreter_path.parts
    if len(parts) < 4:
        raise RuntimeError(
            f"configured command {command!r} interpreter path too short: {interpreter!r}"
        )

    if parts[-3] != "lgrep":
        raise RuntimeError(
            f"configured command {command!r} interpreter is not under a 'lgrep' package directory: {interpreter!r}"
        )
    if parts[-2] != "bin":
        raise RuntimeError(
            f"configured command {command!r} interpreter is not under 'bin': {interpreter!r}"
        )
    if not interpreter_path.is_file():
        raise RuntimeError(
            f"configured command {command!r} interpreter {interpreter!r} does not exist"
        )
    if not os.access(interpreter_path, os.X_OK):
        raise RuntimeError(
            f"configured command {command!r} interpreter {interpreter!r} is not executable"
        )

    uv_tool_dir = str(interpreter_path.parent.parent.parent)
    uv_tool_bin_dir = str(command_path.parent)
    return uv_tool_bin_dir, uv_tool_dir


def _vision_lgrep_port(config_path: Path) -> int:
    """Parse Vision servers.yaml and return the lgrep server port."""
    return _vision_lgrep_server(config_path)[0]


def _mcp_post(
    url: str,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
    timeout: float = 30,
) -> tuple[urllib.request.addinfourl, dict[str, Any]]:
    """POST a JSON-RPC payload and return the raw response plus parsed JSON-RPC data."""
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # MCP Streamable HTTP requires clients to accept BOTH media types; the
        # server rejects a json-only Accept with HTTP 400 before dispatching.
        "Accept": "application/json, text/event-stream",
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    response = urllib.request.urlopen(request, timeout=timeout)
    body = response.read().decode("utf-8")
    if not body.strip():
        return response, {}
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type == "application/json":
        message = json.loads(body)
        if not isinstance(message, dict):
            raise RuntimeError("MCP JSON response is not an object")
        return response, message
    message = _parse_sse_message(body)
    return response, message


def _parse_sse_message(body: str) -> dict[str, Any]:
    """Extract the first ``data:`` JSON payload from an SSE response."""
    for line in body.splitlines():
        if line.startswith("data:"):
            data = line[len("data:") :].strip()
            if data:
                return json.loads(data)
    raise RuntimeError("no SSE data found in response")


def _mcp_initialize(session_url: str) -> str:
    """Initialize an MCP session and return the session id."""
    response, message = _mcp_post(
        session_url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lgrep-deploy-vision", "version": "1.0.0"},
            },
        },
    )
    if "error" in message:
        raise RuntimeError(f"MCP initialize error: {message['error']}")
    session_id = response.headers.get("Mcp-Session-Id")
    if not session_id:
        raise RuntimeError("MCP initialize response missing Mcp-Session-Id header")

    # Notify the server that initialization is complete.
    _mcp_post(
        session_url,
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        session_id=session_id,
        timeout=5,
    )
    return session_id


def _mcp_call_tool(
    session_url: str,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call an MCP tool and return the JSON-RPC result."""
    _response, message = _mcp_post(
        session_url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        session_id=session_id,
        timeout=60,
    )
    if "error" in message:
        raise RuntimeError(f"MCP tool {tool_name!r} error: {message['error']}")
    return message.get("result", {})


def call_vision_mcp_tool(
    config_path: Path,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call an MCP tool through Vision and return the structured result."""
    port = _vision_lgrep_port(config_path)
    session_url = f"http://localhost:{port}/mcp"
    session_id = _mcp_initialize(session_url)
    try:
        return _mcp_call_tool(session_url, session_id, tool_name, arguments or {})
    finally:
        # Best-effort cleanup: MCP has no explicit close; the server times out.
        pass


def run_prune_health_checks(config_path: Path) -> None:
    """Run non-destructive prune checks and verify preview/refusal evidence."""
    for tool_name in ("prune_orphans", "prune_symbols"):
        result = call_vision_mcp_tool(config_path, tool_name)
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise RuntimeError(f"{tool_name}: structuredContent is not a dict ({structured!r})")
        if "refused_reason" not in structured:
            raise RuntimeError(f"{tool_name}: response missing refused_reason")
        if not isinstance(structured["refused_reason"], str):
            raise RuntimeError(f"{tool_name}: refused_reason is not a string")
        if structured.get("dry_run") is not True:
            raise RuntimeError(f"{tool_name}: dry_run is not True ({structured.get('dry_run')!r})")


def deploy(args: argparse.Namespace) -> int:
    """Execute the deployment according to parsed arguments."""
    ok, reason = check_trunk_only_context()
    if not ok:
        print(f"error: unsafe deploy context: {reason}", file=sys.stderr)
        return 1

    try:
        tag = resolve_release_tag(args.tag)
        version = version_from_tag(tag)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] would deploy lgrep {tag} to Vision")
        return 0

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            wheel_path = Path(tmpdir) / f"lgrep-{version}-py3-none-any.whl"
            wheel_url = github_wheel_url(tag, args.repo)
            print(f"downloading {wheel_url}")
            download_wheel(wheel_url, wheel_path)

            print("validating Vision configuration")
            validate_vision_config(args.vision_config)

            print("resolving configured lgrep command")
            _configured_port, configured_command = _vision_lgrep_server(args.vision_config)

            print("deriving uv tool runtime from configured command")
            uv_tool_bin_dir, uv_tool_dir = _derive_uv_paths_from_command(configured_command)

            print("installing lgrep wheel into pinned uv tool runtime")
            install_wheel(wheel_path, uv_tool_bin_dir, uv_tool_dir)

            print("checking installed lgrep version before restart")
            check_installed_version(version, configured_command)

            print("restarting Vision service")
            restart_vision_service()

            print("waiting for Vision health")
            wait_vision_ready(
                args.vision_config,
                retries=args.init_retries,
                delay_seconds=args.init_retry_delay,
            )

            print("running non-destructive MCP health checks")
            run_prune_health_checks(args.vision_config)

        print("deploy healthy")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"error: command failed: {exc.cmd}: {exc.stderr}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Deploy a tagged lgrep release to local Vision safely."
    )
    parser.add_argument("--tag", help="Release tag like vX.Y.Z (default: exact tag at HEAD)")
    parser.add_argument(
        "--repo",
        default="Sharper-Flow/lgrep",
        help="GitHub owner/repo (default: Sharper-Flow/lgrep)",
    )
    parser.add_argument(
        "--vision-config",
        type=Path,
        default=DEFAULT_VISION_CONFIG,
        help=f"Path to Vision servers.yaml (default: {DEFAULT_VISION_CONFIG})",
    )
    parser.add_argument(
        "--init-retries",
        type=int,
        default=1,
        help="Initialization retries after restart (default: 1)",
    )
    parser.add_argument(
        "--init-retry-delay",
        type=float,
        default=5.0,
        help="Seconds between initialization retries (default: 5.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate context without installing or restarting",
    )
    args = parser.parse_args(argv)
    return deploy(args)


if __name__ == "__main__":
    sys.exit(main())
