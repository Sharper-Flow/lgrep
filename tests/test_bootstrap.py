"""Tests for the MCP server bootstrap and transport plumbing.

Verifies that the startup transport is preserved for diagnostics without using
``LGREP_TRANSPORT`` as a side channel.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import lgrep.server.bootstrap as bootstrap_module
from lgrep.server import _startup


class TestBootstrapTransportPlumbing:
    def test_bootstrap_exposes_startup_transport_attribute(self):
        assert hasattr(bootstrap_module, "_startup_transport")
        assert bootstrap_module.get_startup_transport() is None

    @pytest.mark.asyncio
    async def test_lifecycle_startup_reads_bootstrap_transport(self):
        bootstrap_module._startup_transport = "streamable-http"
        try:
            server = MagicMock(name="lgrep")
            ctx = await _startup(server)
            assert ctx.transport == "streamable-http"
        finally:
            bootstrap_module._startup_transport = None

    @pytest.mark.asyncio
    async def test_lifecycle_startup_ignores_lgrep_transport_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``LGREP_TRANSPORT`` must not override the bootstrap transport."""
        monkeypatch.setenv("LGREP_TRANSPORT", "sse")
        bootstrap_module._startup_transport = "stdio"
        try:
            server = MagicMock(name="lgrep")
            ctx = await _startup(server)
            assert ctx.transport == "stdio"
        finally:
            bootstrap_module._startup_transport = None

    def test_run_server_records_internal_transport_not_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LGREP_TRANSPORT", raising=False)

        with patch("lgrep.server.mcp.run") as mock_run:
            bootstrap_module.run_server(transport="streamable-http")

        assert bootstrap_module.get_startup_transport() == "streamable-http"
        assert "LGREP_TRANSPORT" not in os.environ
        mock_run.assert_called_once_with(transport="streamable-http")

    def test_run_server_default_records_stdio_transport(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LGREP_TRANSPORT", raising=False)

        with patch("lgrep.server.mcp.run"):
            bootstrap_module.run_server()

        assert bootstrap_module.get_startup_transport() == "stdio"
        assert "LGREP_TRANSPORT" not in os.environ
