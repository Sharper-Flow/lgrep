"""Tests for the MCP server bootstrap and transport plumbing.

Verifies that the startup transport is preserved for diagnostics without using
``LGREP_TRANSPORT`` as a side channel.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from unittest.mock import MagicMock, patch

import pytest
import structlog

import lgrep.server.bootstrap as bootstrap_module
from lgrep.server import _startup


@pytest.fixture()
def clean_logging_state():
    """Snapshot root logging state; restore it after logging tests.

    structlog stays routed through stdlib logging after teardown: the
    suite's ambient contract is that structlog never writes to stdout, and
    ``structlog.reset_defaults()`` would restore the stdout-printing library
    default that leaks into CLI JSON output (tests/test_cli.py relies on
    this contract).
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        bootstrap_module.shutdown_logging()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


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

    def test_run_server_records_internal_transport_not_env(
        self, monkeypatch: pytest.MonkeyPatch, clean_logging_state
    ):
        monkeypatch.delenv("LGREP_TRANSPORT", raising=False)

        with patch("lgrep.server.mcp.run") as mock_run:
            bootstrap_module.run_server(transport="streamable-http")

        assert bootstrap_module.get_startup_transport() == "streamable-http"
        assert "LGREP_TRANSPORT" not in os.environ
        mock_run.assert_called_once_with(transport="streamable-http")

    def test_run_server_default_records_stdio_transport(
        self, monkeypatch: pytest.MonkeyPatch, clean_logging_state
    ):
        monkeypatch.delenv("LGREP_TRANSPORT", raising=False)

        with patch("lgrep.server.mcp.run"):
            bootstrap_module.run_server()

        assert bootstrap_module.get_startup_transport() == "stdio"
        assert "LGREP_TRANSPORT" not in os.environ


class TestConfigureLogging:
    def test_stderr_is_always_a_sink_and_stdout_never_is(self, clean_logging_state):
        bootstrap_module.configure_logging()
        root = logging.getLogger()
        streams = [getattr(handler, "stream", None) for handler in root.handlers]
        assert sys.stderr in streams
        assert sys.stdout not in streams

    def test_log_file_env_writes_json_lines_and_lock_sidecar(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, clean_logging_state
    ):
        log_file = tmp_path / "lgrep.log"
        monkeypatch.setenv("LGREP_LOG_FILE", str(log_file))

        bootstrap_module.configure_logging()
        structlog.get_logger("tests.probe").info("probe_event", path="/tmp/project")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert (tmp_path / "lgrep.log.lock").exists()
        entries = [json.loads(line) for line in log_file.read_text().splitlines()]
        probes = [entry for entry in entries if entry.get("event") == "probe_event"]
        assert len(probes) == 1
        assert probes[0]["path"] == "/tmp/project"
        assert probes[0]["level"] == "info"
        assert "timestamp" in probes[0]

    def test_second_writer_is_refused_and_keeps_stderr_only(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys, clean_logging_state
    ):
        log_file = tmp_path / "lgrep.log"
        blocker = os.open(str(log_file) + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(blocker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            monkeypatch.setenv("LGREP_LOG_FILE", str(log_file))
            bootstrap_module.configure_logging()
        finally:
            fcntl.flock(blocker, fcntl.LOCK_UN)
            os.close(blocker)

        root = logging.getLogger()
        assert [h for h in root.handlers if isinstance(h, RotatingFileHandler)] == []
        assert sys.stderr in [getattr(h, "stream", None) for h in root.handlers]
        assert str(log_file) in capsys.readouterr().err

    def test_preinstalled_stdout_handler_is_evicted_and_stdout_stays_clean(
        self, monkeypatch: pytest.MonkeyPatch, capsys, clean_logging_state
    ):
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)
        logging.getLogger().handlers[:] = [logging.StreamHandler(sys.stdout)]

        bootstrap_module.configure_logging()
        structlog.get_logger("tests.probe").info("probe_event")
        for handler in logging.getLogger().handlers:
            handler.flush()

        streams = [getattr(handler, "stream", None) for handler in logging.getLogger().handlers]
        assert sys.stdout not in streams
        assert capsys.readouterr().out == ""

    def test_preinstalled_stderr_handler_is_not_duplicated(self, capsys, clean_logging_state):
        logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

        bootstrap_module.configure_logging()
        structlog.get_logger("tests.probe").info("probe_event")
        for handler in logging.getLogger().handlers:
            handler.flush()

        stderr_streams = [
            getattr(handler, "stream", None)
            for handler in logging.getLogger().handlers
            if getattr(handler, "stream", None) is sys.stderr
        ]
        assert len(stderr_streams) == 1
        lines = capsys.readouterr().err.strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "probe_event"

    def test_non_contention_flock_error_reports_actual_cause(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys, clean_logging_state
    ):
        log_file = tmp_path / "lgrep.log"
        monkeypatch.setenv("LGREP_LOG_FILE", str(log_file))

        def injected_eio(fd, op):
            raise OSError(errno.EIO, "Input/output error")

        monkeypatch.setattr(bootstrap_module.fcntl, "flock", injected_eio)
        bootstrap_module.configure_logging()

        root = logging.getLogger()
        assert [h for h in root.handlers if isinstance(h, RotatingFileHandler)] == []
        err = capsys.readouterr().err
        assert "held by another process" not in err
        assert "Input/output error" in err

    def test_missing_log_parent_directory_keeps_stderr_only(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys, clean_logging_state
    ):
        log_file = tmp_path / "missing-parent" / "lgrep.log"
        monkeypatch.setenv("LGREP_LOG_FILE", str(log_file))

        bootstrap_module.configure_logging()

        root = logging.getLogger()
        assert [h for h in root.handlers if isinstance(h, RotatingFileHandler)] == []
        assert sys.stderr in [getattr(h, "stream", None) for h in root.handlers]
        assert str(log_file) in capsys.readouterr().err

    def test_reconfigure_replaces_owned_handlers_without_duplicates(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, clean_logging_state
    ):
        monkeypatch.setenv("LGREP_LOG_FILE", str(tmp_path / "lgrep.log"))

        bootstrap_module.configure_logging()
        first = len(bootstrap_module._owned_handlers)
        bootstrap_module.configure_logging()
        second = len(bootstrap_module._owned_handlers)

        assert first == 2
        assert second == 2
        file_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
