"""CLI output must not depend on an earlier server configuring logging."""

import json
import os
import subprocess
import sys

import pytest


def _run(args, level):
    env = {**os.environ, "LGREP_LOG_LEVEL": level}
    # The daemon's optional file sink must not be opened by these test processes.
    env.pop("LGREP_LOG_FILE", None)
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, env=env, timeout=30
    )


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "ERROR"])
def test_symbol_commands_emit_only_json_on_stdout(tmp_path, level):
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.py").write_text("def cli_probe():\n    return 1\n")
    storage = tmp_path / "symbols"

    indexed = _run(
        ["-m", "lgrep", "index-symbols", str(project), "--storage-dir", str(storage)], level
    )
    assert indexed.returncode == 0, indexed.stderr
    assert json.loads(indexed.stdout)["files_indexed"] == 1
    if level == "ERROR":
        assert indexed.stderr == ""
    else:
        events = [json.loads(line) for line in indexed.stderr.splitlines()]
        assert any(event["level"] == "info" for event in events)
        assert any(event["level"] == "debug" for event in events) == (level == "DEBUG")

    # Force a refresh so search has real log events, not a silent cache hit.
    (project / "example.py").write_text("def cli_probe():\n    return 2\n")
    searched = _run(
        [
            "-m",
            "lgrep",
            "search-symbols",
            "cli_probe",
            str(project),
            "--storage-dir",
            str(storage),
        ],
        level,
    )
    assert searched.returncode == 0, searched.stderr
    assert any(symbol["name"] == "cli_probe" for symbol in json.loads(searched.stdout)["results"])
    events = [json.loads(line) for line in searched.stderr.splitlines()]
    assert any(event["level"] == "debug" for event in events) == (level == "DEBUG")
    if level == "ERROR":
        assert events == []


@pytest.mark.parametrize("command", ["search-semantic", "index-semantic"])
@pytest.mark.parametrize("level", ["DEBUG", "INFO", "ERROR"])
def test_semantic_commands_filter_logs_without_hiding_json_errors(tmp_path, command, level):
    script = """
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import structlog
from lgrep.cli import main

# An embedding failure is local and deterministic; never call the paid service.
def fail(**kwargs):
    log = structlog.get_logger()
    log.debug('cli_probe_debug')
    log.info('cli_probe_info')
    log.error('cli_probe_error')
    raise RuntimeError('offline embedding failure')

logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
with (
    patch('lgrep.embeddings.VoyageEmbedder', side_effect=fail),
    patch('lgrep.storage.get_project_db_path', return_value=Path(sys.argv[-1])),
):
    sys.exit(main())
"""
    args = ["-c", script, command]
    if command == "search-semantic":
        args.append("query")
    args.append(str(tmp_path))
    # Supply no real credential, even if the parent process has one.
    with pytest.MonkeyPatch.context() as env:
        env.setenv("VOYAGE_API_KEY", "test-not-a-real-key")
        result = _run(args, level)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout) == {"error": "offline embedding failure"}
    events = [json.loads(line) for line in result.stderr.splitlines()]
    expected = {
        "DEBUG": ["cli_probe_debug", "cli_probe_info", "cli_probe_error"],
        "INFO": ["cli_probe_info", "cli_probe_error"],
        "ERROR": ["cli_probe_error"],
    }
    assert [event["event"] for event in events] == expected[level]
