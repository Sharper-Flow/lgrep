"""Tests for the safe Vision deploy command.

These tests treat ``scripts/deploy_vision.py`` as the artifact under test. They
verify trunk-only safety, bounded initialization retry, exact version checks,
and non-destructive MCP health evidence without touching a real Vision service.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def _load_deploy_module() -> ModuleType:
    """Load the standalone deploy script as a module for testing."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "deploy_vision.py"
    spec = importlib.util.spec_from_file_location("deploy_vision", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_vision"] = module
    spec.loader.exec_module(module)
    return module


dv = _load_deploy_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_namespace(**kwargs: Any) -> argparse.Namespace:
    defaults = {
        "tag": None,
        "repo": "Sharper-Flow/lgrep",
        "vision_config": Path("/tmp/vision/servers.yaml"),
        "init_retries": 1,
        "init_retry_delay": 0.0,
        "dry_run": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# UV path derivation from configured launcher
# ---------------------------------------------------------------------------


def _make_uv_launcher(tmp_path: Path, name: str = "lgrep", tool_dir: Path | None = None) -> Path:
    """Create a launcher script with a uv-style shebang pointing at an lgrep tool dir."""
    exe = tmp_path / "bin" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    if tool_dir is None:
        tool_dir = tmp_path / "tools"
    interpreter = tool_dir / "lgrep" / "bin" / "python"
    exe.write_text(f"#!{interpreter}\n")
    exe.chmod(0o755)
    return exe


def test_derive_uv_paths_from_command_succeeds(tmp_path: Path) -> None:
    exe = _make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools")
    uv_tool_bin_dir, uv_tool_dir = dv._derive_uv_paths_from_command(str(exe))
    assert uv_tool_bin_dir == str(tmp_path / "bin")
    assert uv_tool_dir == str(tmp_path / "tools")


def test_derive_uv_paths_from_command_rejects_non_shebang(tmp_path: Path) -> None:
    exe = tmp_path / "lgrep"
    exe.write_text("not a shebang launcher")
    exe.chmod(0o755)
    with pytest.raises(RuntimeError, match="not a shebang launcher"):
        dv._derive_uv_paths_from_command(str(exe))


def test_derive_uv_paths_from_command_rejects_wrong_package_directory(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "lgrep"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"#!{tmp_path / 'tools' / 'wrongpkg' / 'bin' / 'python'}\n")
    exe.chmod(0o755)
    with pytest.raises(RuntimeError, match="not under a 'lgrep' package directory"):
        dv._derive_uv_paths_from_command(str(exe))


def test_derive_uv_paths_from_command_rejects_interpreter_not_under_bin(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "lgrep"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"#!{tmp_path / 'tools' / 'lgrep' / 'lib' / 'python'}\n")
    exe.chmod(0o755)
    with pytest.raises(RuntimeError, match="not under 'bin'"):
        dv._derive_uv_paths_from_command(str(exe))


def test_derive_uv_paths_from_command_rejects_short_interpreter_path(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "lgrep"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/tmp/python\n")
    exe.chmod(0o755)
    with pytest.raises(RuntimeError, match="interpreter path too short"):
        dv._derive_uv_paths_from_command(str(exe))


# ---------------------------------------------------------------------------
# Subprocess fakes
# ---------------------------------------------------------------------------


class FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_run_for_trunk_ok(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
    # cmd is like ["git", "-C", "/path", "rev-parse", ...]
    joined = " ".join(cmd)
    if "rev-parse --is-inside-work-tree" in joined:
        return FakeCompletedProcess("true")
    if "branch --show-current" in joined:
        return FakeCompletedProcess("main")
    if "status --porcelain" in joined:
        return FakeCompletedProcess("")
    if "rev-parse --git-dir" in joined:
        return FakeCompletedProcess("/repo/.git")
    if "rev-parse --git-common-dir" in joined:
        return FakeCompletedProcess("/repo/.git")
    return FakeCompletedProcess("")


def _fake_run_for_wrong_branch(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
    joined = " ".join(cmd)
    if "rev-parse --is-inside-work-tree" in joined:
        return FakeCompletedProcess("true")
    if "branch --show-current" in joined:
        return FakeCompletedProcess("feature")
    return _fake_run_for_trunk_ok(cmd, **kwargs)


def _fake_run_for_dirty(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
    joined = " ".join(cmd)
    if "status --porcelain" in joined:
        return FakeCompletedProcess(" M src/file.py")
    return _fake_run_for_trunk_ok(cmd, **kwargs)


def _fake_run_for_worktree(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
    joined = " ".join(cmd)
    if "rev-parse --git-dir" in joined:
        return FakeCompletedProcess("/repo/.git/worktrees/fix")
    if "rev-parse --git-common-dir" in joined:
        return FakeCompletedProcess("/repo/.git")
    return _fake_run_for_trunk_ok(cmd, **kwargs)


def _fake_run_for_tag_at_head(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
    joined = " ".join(cmd)
    if "describe --tags --exact-match" in joined:
        return FakeCompletedProcess("v3.2.5")
    return _fake_run_for_trunk_ok(cmd, **kwargs)


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def test_trunk_only_context_accepts_clean_main() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_trunk_ok):
        ok, reason = dv.check_trunk_only_context("/repo")
    assert ok is True
    assert reason == ""


def test_trunk_only_context_rejects_non_main_branch() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_wrong_branch):
        ok, reason = dv.check_trunk_only_context("/repo")
    assert ok is False
    assert "branch is 'feature'" in reason


def test_trunk_only_context_rejects_dirty_tree() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_dirty):
        ok, reason = dv.check_trunk_only_context("/repo")
    assert ok is False
    assert "uncommitted changes" in reason


def test_trunk_only_context_rejects_worktree() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_worktree):
        ok, reason = dv.check_trunk_only_context("/repo")
    assert ok is False
    assert "worktree" in reason


def test_resolve_release_tag_validates_format() -> None:
    with pytest.raises(RuntimeError, match="does not match vX.Y.Z"):
        dv.resolve_release_tag("3.2.5")


def test_resolve_release_tag_reads_head() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_tag_at_head):
        assert dv.resolve_release_tag(None, "/repo") == "v3.2.5"


def test_resolve_release_tag_accepts_explicit() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_trunk_ok):
        assert dv.resolve_release_tag("v1.2.3", "/repo") == "v1.2.3"


def test_github_wheel_url() -> None:
    url = dv.github_wheel_url("v3.2.5", "Sharper-Flow/lgrep")
    assert url == (
        "https://github.com/Sharper-Flow/lgrep/releases/download/"
        "v3.2.5/lgrep-3.2.5-py3-none-any.whl"
    )


# ---------------------------------------------------------------------------
# Initialization retry
# ---------------------------------------------------------------------------


def test_wait_vision_ready_succeeds_first_try() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        return FakeCompletedProcess("")

    with patch.object(dv, "_run", side_effect=fake_run):
        dv.wait_vision_ready(Path("/tmp/vision/servers.yaml"))

    assert len(calls) == 1
    assert calls[0][:3] == ["vision", "health", "--config"]


def test_wait_vision_ready_retries_once_then_succeeds() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, cmd, stderr="timeout")
        return FakeCompletedProcess("")

    with patch.object(dv, "_run", side_effect=fake_run):
        dv.wait_vision_ready(Path("/tmp/vision/servers.yaml"), retries=1, delay_seconds=0)

    assert len(calls) == 2


def test_wait_vision_ready_fails_after_exhausting_retries() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        raise subprocess.CalledProcessError(1, cmd, stderr="timeout")

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        pytest.raises(RuntimeError, match="Vision did not become healthy"),
    ):
        dv.wait_vision_ready(Path("/tmp/vision/servers.yaml"), retries=1, delay_seconds=0)

    # Initial attempt + one retry = 2 calls.
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------


def test_check_installed_version_matches() -> None:
    with patch.object(dv, "_run", return_value=FakeCompletedProcess("lgrep 3.2.5\n")):
        dv.check_installed_version("3.2.5", "lgrep")


def test_check_installed_version_mismatch() -> None:
    with (
        patch.object(dv, "_run", return_value=FakeCompletedProcess("lgrep 3.2.4\n")),
        pytest.raises(RuntimeError, match="does not match"),
    ):
        dv.check_installed_version("3.2.5", "lgrep")


def test_install_wheel_passes_pinned_env() -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return FakeCompletedProcess("")

    with patch.object(dv, "_run", side_effect=fake_run):
        dv.install_wheel(Path("/tmp/lgrep-3.2.5-py3-none-any.whl"), "/tmp/bin", "/tmp/tools")

    assert captured["cmd"] == [
        "uv",
        "tool",
        "install",
        "--reinstall",
        "/tmp/lgrep-3.2.5-py3-none-any.whl",
    ]
    env = captured["env"]
    assert env is not None
    assert env["UV_TOOL_BIN_DIR"] == "/tmp/bin"
    assert env["UV_TOOL_DIR"] == "/tmp/tools"


# ---------------------------------------------------------------------------
# Configured lgrep command resolution
# ---------------------------------------------------------------------------


def _make_executable(tmp_path: Path, name: str = "lgrep") -> Path:
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\necho lgrep 3.2.5\n")
    exe.chmod(0o755)
    return exe


def _write_servers_yaml(tmp_path: Path, command: str | None, port: Any = 6278) -> Path:
    config = tmp_path / "servers.yaml"
    port_value = f'"{port}"' if isinstance(port, str) else str(port)
    lines = ["servers:", "  lgrep:", f"    port: {port_value}"]
    if command is not None:
        lines.append(f"    command: {command}")
    config.write_text("\n".join(lines) + "\n")
    return config


def test_vision_lgrep_server_returns_port_and_command(tmp_path: Path) -> None:
    exe = _make_executable(tmp_path)
    config = _write_servers_yaml(tmp_path, str(exe))
    port, command = dv._vision_lgrep_server(config)
    assert port == 6278
    assert command == str(exe)


def test_vision_lgrep_server_rejects_missing_server(tmp_path: Path) -> None:
    config = tmp_path / "servers.yaml"
    config.write_text("servers:\n  other:\n    port: 1234\n")
    with pytest.raises(RuntimeError, match="server 'lgrep' not found"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_missing_command(tmp_path: Path) -> None:
    config = _write_servers_yaml(tmp_path, command=None)
    with pytest.raises(RuntimeError, match="missing command"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_non_string_command(tmp_path: Path) -> None:
    config = tmp_path / "servers.yaml"
    config.write_text("servers:\n  lgrep:\n    port: 6278\n    command: [1, 2]\n")
    with pytest.raises(RuntimeError, match="command is not a string"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_relative_command(tmp_path: Path) -> None:
    config = _write_servers_yaml(tmp_path, command="lgrep")
    with pytest.raises(RuntimeError, match="not an absolute path"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_nonexistent_command(tmp_path: Path) -> None:
    config = _write_servers_yaml(tmp_path, command="/does/not/exist")
    with pytest.raises(RuntimeError, match="does not exist"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_non_executable_command(tmp_path: Path) -> None:
    exe = tmp_path / "lgrep"
    exe.write_text("no execute bit")
    config = _write_servers_yaml(tmp_path, command=str(exe))
    with pytest.raises(RuntimeError, match="not executable"):
        dv._vision_lgrep_server(config)


def test_vision_lgrep_server_rejects_invalid_port(tmp_path: Path) -> None:
    exe = _make_executable(tmp_path)
    config = _write_servers_yaml(tmp_path, command=str(exe), port="6278")
    with pytest.raises(RuntimeError, match="invalid port"):
        dv._vision_lgrep_server(config)


def test_deploy_uses_configured_command_not_ambient_path(tmp_path: Path) -> None:
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools"))

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        joined = " ".join(cmd)
        if "git" in joined:
            return _fake_run_for_trunk_ok(cmd, **kwargs)
        if "vision config validate" in joined:
            return FakeCompletedProcess("")
        if "uv tool install" in joined:
            return FakeCompletedProcess("")
        if "systemctl --user restart" in joined:
            return FakeCompletedProcess("")
        if "vision health" in joined:
            return FakeCompletedProcess("")
        if joined.startswith(f"{configured_command} --version"):
            return FakeCompletedProcess("lgrep 3.2.5\n")
        # Any call to the ambient ``lgrep`` executable is a PATH-divergence bug.
        if cmd and cmd[0] == "lgrep":
            raise RuntimeError("ambient lgrep was invoked unexpectedly")
        return FakeCompletedProcess("")

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result("prune_orphans"),
                    "prune_symbols": _make_mcp_tool_result("prune_symbols"),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 0


def test_deploy_rejects_invalid_command_before_restart() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        joined = " ".join(cmd)
        if "git" in joined:
            return _fake_run_for_trunk_ok(cmd, **kwargs)
        if "vision config validate" in joined:
            return FakeCompletedProcess("")
        if "systemctl --user restart" in joined:
            return FakeCompletedProcess("")
        return FakeCompletedProcess("")

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        patch.object(
            dv, "_vision_lgrep_server", side_effect=RuntimeError("configured command missing")
        ),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 1

    assert not any("systemctl --user restart" in " ".join(c) for c in calls)


# ---------------------------------------------------------------------------
# MCP health checks
# ---------------------------------------------------------------------------


def _make_sse_response(data: dict[str, Any]) -> str:
    return f"event: message\ndata: {json.dumps(data)}\n\n"


def _request_body(request: Any) -> dict[str, Any]:
    """Decode the JSON body of a urllib Request for assertion helpers."""
    return json.loads(request.data.decode("utf-8"))


def _make_mcp_tool_result(
    tool_name: str, dry_run: bool = True, include_refused_reason: bool = True
) -> dict[str, Any]:
    structured: dict[str, Any] = {
        "dry_run": dry_run,
        "tool": tool_name,
    }
    if include_refused_reason:
        structured["refused_reason"] = ""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [{"type": "text", "text": json.dumps(structured)}],
            "structuredContent": structured,
        },
    }


def test_mcp_post_accepts_json_response() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.headers = {"Content-Type": "application/json"}

    with patch.object(dv.urllib.request, "urlopen", return_value=response):
        _response, message = dv._mcp_post("http://localhost:6278/mcp", payload)

    assert message == payload


def test_mcp_post_accept_header_allows_json_and_sse() -> None:
    """MCP Streamable HTTP rejects a json-only Accept with HTTP 400.

    The server requires the client to advertise both media types before it will
    dispatch the request, so a json-only Accept makes every health check fail.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.headers = {"Content-Type": "application/json"}
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, **kwargs: Any) -> Any:
        captured["request"] = request
        return response

    with patch.object(dv.urllib.request, "urlopen", side_effect=fake_urlopen):
        dv._mcp_post("http://localhost:6278/mcp", payload)

    accept = captured["request"].get_header("Accept")
    assert "application/json" in accept, accept
    assert "text/event-stream" in accept, accept


def _mcp_urlopen_effect(
    tool_results: dict[str, dict[str, Any]] | None = None,
    init_session_id: str = "sid",
) -> Any:
    """Return a fake urlopen that handles MCP initialize/notification/call."""
    tool_results = tool_results or {}

    def fake_urlopen(request: Any, **kwargs: Any) -> Any:
        body = _request_body(request)
        method = body.get("method", "")
        mock = MagicMock()

        if method == "initialize":
            mock.read.return_value = _make_sse_response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "vision-proxy-lgrep"},
                    },
                }
            ).encode("utf-8")
            mock.headers = {"Mcp-Session-Id": init_session_id}
            return mock

        if method == "notifications/initialized":
            mock.read.return_value = b""
            mock.headers = {}
            return mock

        if method == "tools/call":
            tool_name = body["params"]["name"]
            result = tool_results.get(tool_name, _make_mcp_tool_result(tool_name))
            mock.read.return_value = _make_sse_response(result).encode("utf-8")
            mock.headers = {}
            return mock

        raise RuntimeError(f"unexpected MCP method: {method}")

    return fake_urlopen


# ---------------------------------------------------------------------------
# MCP health checks
# ---------------------------------------------------------------------------


def test_run_prune_health_checks_accepts_valid_response() -> None:
    with (
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result("prune_orphans"),
                    "prune_symbols": _make_mcp_tool_result("prune_symbols"),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_port", return_value=6278),
    ):
        dv.run_prune_health_checks(Path("/tmp/vision/servers.yaml"))


def test_run_prune_health_checks_rejects_missing_refused_reason() -> None:
    with (
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result(
                        "prune_orphans", include_refused_reason=False
                    ),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_port", return_value=6278),
        pytest.raises(RuntimeError, match="missing refused_reason"),
    ):
        dv.run_prune_health_checks(Path("/tmp/vision/servers.yaml"))


def test_run_prune_health_checks_rejects_non_dry_run() -> None:
    with (
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result("prune_orphans", dry_run=False),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_port", return_value=6278),
        pytest.raises(RuntimeError, match="dry_run is not True"),
    ):
        dv.run_prune_health_checks(Path("/tmp/vision/servers.yaml"))


# ---------------------------------------------------------------------------
# End-to-end deploy() flow
# ---------------------------------------------------------------------------


def test_deploy_dry_run_succeeds_from_clean_main() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_tag_at_head):
        args = _make_namespace(dry_run=True)
        assert dv.deploy(args) == 0


def test_deploy_refuses_unsafe_context() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_wrong_branch):
        args = _make_namespace(tag="v1.2.3")
        assert dv.deploy(args) == 1


def _fake_run_full_deploy(
    version: str = "3.2.5",
    configured_command: str = "/configured/lgrep",
) -> Any:
    """Return a fake _run that handles the full deploy command list."""
    calls: list[list[str]] = []
    env_calls: list[dict[str, str] | None] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        env_calls.append(kwargs.get("env"))
        joined = " ".join(cmd)

        # Git safety / tag commands
        if "git" in joined:
            if "rev-parse --is-inside-work-tree" in joined:
                return FakeCompletedProcess("true")
            if "branch --show-current" in joined:
                return FakeCompletedProcess("main")
            if "status --porcelain" in joined:
                return FakeCompletedProcess("")
            if "rev-parse --git-dir" in joined:
                return FakeCompletedProcess("/repo/.git")
            if "rev-parse --git-common-dir" in joined:
                return FakeCompletedProcess("/repo/.git")
            if "describe --tags --exact-match" in joined:
                return FakeCompletedProcess(f"v{version}")

        # Vision / uv / systemctl / configured lgrep commands
        if "vision config validate" in joined:
            return FakeCompletedProcess("")
        if "uv tool install" in joined:
            return FakeCompletedProcess("")
        if "systemctl --user restart" in joined:
            return FakeCompletedProcess("")
        if "vision health" in joined:
            return FakeCompletedProcess("")
        if joined.startswith(f"{configured_command} --version"):
            return FakeCompletedProcess(f"lgrep {version}\n")

        return FakeCompletedProcess("")

    return fake_run, calls, env_calls


def test_deploy_full_flow_succeeds(tmp_path: Path) -> None:
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools"))
    fake_run, calls, env_calls = _fake_run_full_deploy(configured_command=configured_command)

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result("prune_orphans"),
                    "prune_symbols": _make_mcp_tool_result("prune_symbols"),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace()
        assert dv.deploy(args) == 0

    assert any("vision config validate" in " ".join(c) for c in calls)
    assert any("uv tool install" in " ".join(c) for c in calls)
    assert any("systemctl --user restart" in " ".join(c) for c in calls)
    assert any("vision health" in " ".join(c) for c in calls)
    assert any(f"{configured_command} --version" in " ".join(c) for c in calls)


@patch.dict(
    os.environ,
    {"UV_TOOL_DIR": "/wrong/tools", "UV_TOOL_BIN_DIR": "/wrong/bin"},
    clear=False,
)
def test_deploy_pinned_env_overrides_ambient(tmp_path: Path) -> None:
    """Ambient UV tool paths must not leak into the uv install child environment."""
    tool_dir = tmp_path / "tools"
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tool_dir))
    fake_run, _calls, env_calls = _fake_run_full_deploy(configured_command=configured_command)

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result("prune_orphans"),
                    "prune_symbols": _make_mcp_tool_result("prune_symbols"),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 0

    install_env = next(
        env for env in env_calls if env is not None and env.get("UV_TOOL_DIR") is not None
    )
    assert install_env is not None
    assert install_env["UV_TOOL_DIR"] == str(tool_dir)
    assert install_env["UV_TOOL_BIN_DIR"] == str(tmp_path / "bin")
    assert install_env["UV_TOOL_DIR"] != "/wrong/tools"
    assert install_env["UV_TOOL_BIN_DIR"] != "/wrong/bin"


def test_deploy_version_check_before_restart_catches_mismatch(tmp_path: Path) -> None:
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools"))
    fake_run, _calls, _env_calls = _fake_run_full_deploy(
        version="3.2.5", configured_command=configured_command
    )

    # Make version check return a different version for the configured command.
    def patched_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        joined = " ".join(cmd)
        if joined.startswith(f"{configured_command} --version"):
            return FakeCompletedProcess("lgrep 3.2.4\n")
        return fake_run(cmd, **kwargs)

    calls: list[list[str]] = []

    def capturing_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        return patched_run(cmd, **kwargs)

    with (
        patch.object(dv, "_run", side_effect=capturing_run),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 1

    assert any(f"{configured_command} --version" in " ".join(c) for c in calls)
    assert not any("systemctl --user restart" in " ".join(c) for c in calls)


def test_deploy_fails_when_version_mismatch(tmp_path: Path) -> None:
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools"))
    fake_run, _calls, _env_calls = _fake_run_full_deploy(
        version="3.2.5", configured_command=configured_command
    )

    # Make version check return a different version for the configured command.
    def patched_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        joined = " ".join(cmd)
        if joined.startswith(f"{configured_command} --version"):
            return FakeCompletedProcess("lgrep 3.2.4\n")
        return fake_run(cmd, **kwargs)

    with (
        patch.object(dv, "_run", side_effect=patched_run),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 1


def test_deploy_fails_when_health_check_lacks_refused_reason(tmp_path: Path) -> None:
    configured_command = str(_make_uv_launcher(tmp_path, tool_dir=tmp_path / "tools"))
    fake_run, _calls, _env_calls = _fake_run_full_deploy(configured_command=configured_command)

    with (
        patch.object(dv, "_run", side_effect=fake_run),
        patch.object(
            dv.urllib.request,
            "urlopen",
            side_effect=_mcp_urlopen_effect(
                tool_results={
                    "prune_orphans": _make_mcp_tool_result(
                        "prune_orphans", include_refused_reason=False
                    ),
                }
            ),
        ),
        patch.object(dv, "_vision_lgrep_server", return_value=(6278, configured_command)),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_dry_run_invokes_deploy() -> None:
    with (
        patch.object(dv, "_run", side_effect=_fake_run_for_tag_at_head),
        patch("sys.stdout", new_callable=StringIO) as stdout,
    ):
        assert dv.main(["--dry-run"]) == 0
        assert "would deploy" in stdout.getvalue()


def test_main_invalid_tag_exits_nonzero() -> None:
    with patch.object(dv, "_run", side_effect=_fake_run_for_trunk_ok):
        assert dv.main(["--tag", "not-a-tag"]) == 1
