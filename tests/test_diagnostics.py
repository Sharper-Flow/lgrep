"""Tests for lgrep_diagnostics MCP tool.

Verifies response shape, secret exclusion, and RuntimeSupervisor integration.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import Context

from lgrep.server import LgrepContext, mcp
from lgrep.server.runtime import RuntimeSupervisor


class TestDiagnosticsToolRegistration:
    def test_diagnostics_tool_registered(self):
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        assert "lgrep_diagnostics" in registered, (
            f"lgrep_diagnostics not registered. Tools: {registered}"
        )


class TestDiagnosticsResponseShape:
    @pytest.mark.asyncio
    async def test_diagnostics_returns_required_fields(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        assert isinstance(result, dict)

        required = {
            "pid",
            "uptime_seconds",
            "transport",
            "worker_max_threads",
            "active_job_count",
            "recent_job_count",
            "loaded_project_count",
            "loaded_projects",
            "active_jobs",
            "recent_jobs",
            "timeout_abandonment_summary",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing fields: {missing}"

    @pytest.mark.asyncio
    async def test_diagnostics_loaded_projects_have_required_fields(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        for project in result["loaded_projects"]:
            assert "path" in project
            assert "watching" in project
            assert os.path.isabs(project["path"]), (
                f"Project path must be absolute: {project['path']}"
            )

    @pytest.mark.asyncio
    async def test_diagnostics_jobs_have_required_fields(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        for job in result["active_jobs"]:
            assert "id" in job
            assert "kind" in job
            assert "status" in job
            assert "age_ms" in job
        for job in result["recent_jobs"]:
            assert "id" in job
            assert "kind" in job
            assert "status" in job
            assert "age_ms" in job

    @pytest.mark.asyncio
    async def test_diagnostics_timeout_summary_has_required_fields(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        summary = result["timeout_abandonment_summary"]
        assert "abandoned_count" in summary
        assert "finished_after_abandon_count" in summary
        assert "failed_after_abandon_count" in summary

    @pytest.mark.asyncio
    async def test_diagnostics_with_context_reflects_loaded_projects(self, tmp_path):
        app_ctx = LgrepContext(voyage_api_key="mock-key")
        app_ctx.projects[str(tmp_path.resolve())] = MagicMock(watching=True)

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context.lifespan_context = app_ctx

        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn(ctx=mock_ctx)

        assert result["loaded_project_count"] >= 1
        paths = {p["path"] for p in result["loaded_projects"]}
        assert str(tmp_path.resolve()) in paths

    @pytest.mark.asyncio
    async def test_diagnostics_reflects_runtime_supervisor_jobs(self):
        supervisor = RuntimeSupervisor(max_workers=1, history_limit=10)
        app_ctx = LgrepContext(runtime=supervisor)

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context.lifespan_context = app_ctx

        # Run a blocking job so we have something in recent
        def quick_work():
            return "done"

        await supervisor.run_blocking(
            kind="test", caller="test", project="/tmp/project", fn=quick_work
        )

        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn(ctx=mock_ctx)

        assert result["recent_job_count"] >= 1
        assert len(result["recent_jobs"]) >= 1
        assert result["worker_max_threads"] == 1

        supervisor.shutdown(cancel_futures=True)

    def _get_tool_fn(self, name: str):
        for t in mcp._tool_manager.list_tools():
            if t.name == name:
                return t.fn
        raise KeyError(f"Tool not found: {name}")


class TestDiagnosticsSecretExclusion:
    @pytest.mark.asyncio
    async def test_diagnostics_does_not_expose_voyage_api_key(self, tmp_path):
        app_ctx = LgrepContext(voyage_api_key="secret-key-123")
        mock_ctx = MagicMock(spec=Context)
        mock_ctx.request_context.lifespan_context = app_ctx

        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn(ctx=mock_ctx)
        result_str = str(result)
        assert "secret-key-123" not in result_str
        assert "voyage_api_key" not in result_str.lower()

    @pytest.mark.asyncio
    async def test_diagnostics_does_not_expose_env_vars(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        result_str = str(result)
        # Should not contain common env var patterns
        assert "VOYAGE_API_KEY" not in result_str
        assert "LGREP_WORKER_MAX_THREADS" not in result_str
        assert "PATH=" not in result_str
        assert "HOME=" not in result_str

    @pytest.mark.asyncio
    async def test_diagnostics_does_not_contain_raw_tracebacks(self, tmp_path):
        fn = self._get_tool_fn("lgrep_diagnostics")
        result = await fn()
        result_str = str(result)
        assert "Traceback (most recent call last)" not in result_str
        assert 'File "' not in result_str

    def _get_tool_fn(self, name: str):
        for t in mcp._tool_manager.list_tools():
            if t.name == name:
                return t.fn
        raise KeyError(f"Tool not found: {name}")
