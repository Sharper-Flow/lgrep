"""Tests for the safe Vision deploy command.

These tests treat ``scripts/deploy_vision.py`` as the artifact under test. They
verify trunk-only safety, bounded initialization retry, exact version checks,
and non-destructive MCP health evidence without touching a real Vision service.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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
        dv.check_installed_version("3.2.5")


def test_check_installed_version_mismatch() -> None:
    with (
        patch.object(dv, "_run", return_value=FakeCompletedProcess("lgrep 3.2.4\n")),
        pytest.raises(RuntimeError, match="does not match"),
    ):
        dv.check_installed_version("3.2.5")


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
) -> Any:
    """Return a fake _run that handles the full deploy command list."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
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

        # Vision / uv / systemctl / lgrep commands
        if "vision config validate" in joined:
            return FakeCompletedProcess("")
        if "uv tool install" in joined:
            return FakeCompletedProcess("")
        if "systemctl --user restart" in joined:
            return FakeCompletedProcess("")
        if "vision health" in joined:
            return FakeCompletedProcess("")
        if "lgrep --version" in joined:
            return FakeCompletedProcess(f"lgrep {version}\n")

        return FakeCompletedProcess("")

    return fake_run, calls


def test_deploy_full_flow_succeeds() -> None:
    fake_run, calls = _fake_run_full_deploy()

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
        patch.object(dv, "_vision_lgrep_port", return_value=6278),
        patch.object(dv, "download_wheel"),
    ):
        args = _make_namespace()
        assert dv.deploy(args) == 0

    assert any("vision config validate" in " ".join(c) for c in calls)
    assert any("uv tool install" in " ".join(c) for c in calls)
    assert any("systemctl --user restart" in " ".join(c) for c in calls)
    assert any("vision health" in " ".join(c) for c in calls)
    assert any("lgrep --version" in " ".join(c) for c in calls)


def test_deploy_fails_when_version_mismatch() -> None:
    fake_run, _calls = _fake_run_full_deploy(version="3.2.5")

    # Make version check return a different version.
    def patched_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        joined = " ".join(cmd)
        if "lgrep --version" in joined:
            return FakeCompletedProcess("lgrep 3.2.4\n")
        return fake_run(cmd, **kwargs)

    with patch.object(dv, "_run", side_effect=patched_run), patch.object(dv, "download_wheel"):
        args = _make_namespace(tag="v3.2.5")
        assert dv.deploy(args) == 1


def test_deploy_fails_when_health_check_lacks_refused_reason() -> None:
    fake_run, _calls = _fake_run_full_deploy()

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
        patch.object(dv, "_vision_lgrep_port", return_value=6278),
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
