"""CLI output must not depend on an earlier server configuring logging."""

import json
import os
import subprocess
import sys

import pytest


def _run(args, level, log_file):
    env = {**os.environ, "LGREP_LOG_LEVEL": level, "LGREP_LOG_FILE": str(log_file)}
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, env=env, timeout=30
    )


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "ERROR"])
def test_symbol_commands_emit_only_json_on_stdout(tmp_path, level):
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.py").write_text("def cli_probe():\n    return 1\n")
    storage = tmp_path / "symbols"
    log_file = tmp_path / "daemon.log"

    indexed = _run(
        ["-m", "lgrep", "index-symbols", str(project), "--storage-dir", str(storage)],
        level,
        log_file,
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
        log_file,
    )
    assert searched.returncode == 0, searched.stderr
    assert any(symbol["name"] == "cli_probe" for symbol in json.loads(searched.stdout)["results"])
    events = [json.loads(line) for line in searched.stderr.splitlines()]
    assert any(event["level"] == "debug" for event in events) == (level == "DEBUG")
    if level == "ERROR":
        assert events == []
    assert not log_file.exists()
    assert not log_file.with_suffix(".log.lock").exists()


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
        result = _run(args, level, tmp_path / "daemon.log")
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout) == {"error": "offline embedding failure"}
    events = [json.loads(line) for line in result.stderr.splitlines()]
    expected = {
        "DEBUG": ["cli_probe_debug", "cli_probe_info", "cli_probe_error"],
        "INFO": ["cli_probe_info", "cli_probe_error"],
        "ERROR": ["cli_probe_error"],
    }
    assert [event["event"] for event in events] == expected[level]
    assert not (tmp_path / "daemon.log").exists()
    assert not (tmp_path / "daemon.log.lock").exists()


def test_symbol_cli_does_not_import_the_server_or_semantic_stack(tmp_path):
    script = """
import sys
from lgrep.cli import main

assert main() == 1
for name in ('lgrep.server', 'lgrep.indexing', 'lancedb', 'mcp'):
    assert name not in sys.modules, name
"""
    result = _run(
        [
            "-c",
            script,
            "search-symbols",
            "probe",
            str(tmp_path),
            "--storage-dir",
            str(tmp_path / "symbols"),
        ],
        "ERROR",
        tmp_path / "daemon.log",
    )
    assert result.returncode == 0, result.stderr
    assert "error" in json.loads(result.stdout)


@pytest.mark.parametrize("command", ["gc", "prune-orphans", "prune-symbols"])
@pytest.mark.parametrize("level, execute", [("DEBUG", False), ("INFO", True), ("ERROR", True)])
def test_maintenance_reports_keep_logs_off_stdout(tmp_path, command, level, execute):
    script = """
import logging
import sys
from unittest.mock import patch

import structlog
from lgrep.cli import main

def report(*, dry_run, **kwargs):
    log = structlog.get_logger()
    log.debug('maintenance_debug')
    log.info('maintenance_info')
    log.warning('maintenance_warning')
    log.error('maintenance_error')
    logging.getLogger('maintenance').warning('maintenance_stdlib_warning')
    return {'dry_run': dry_run, 'failures': ['offline maintenance failure']}

logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
with (
    patch('lgrep.tools.prune_orphans.prune_orphans', side_effect=report),
    patch('lgrep.tools.prune_orphans.gc_worktree_meta', side_effect=report),
    patch('lgrep.tools.prune_orphans.optimize_caches', side_effect=report),
    patch('lgrep.tools.prune_symbols.prune_symbols', side_effect=report),
):
    code = main()
assert 'lgrep.server' not in sys.modules
assert 'mcp' not in sys.modules
sys.exit(code)
"""
    args = ["-c", script, command]
    if command == "prune-symbols":
        args.extend(["--storage-dir", str(tmp_path / "symbols")])
    else:
        args.extend(["--cache-dir", str(tmp_path / "cache")])
    if command == "gc":
        args.extend(["--symbols-dir", str(tmp_path / "symbols")])
    if execute:
        args.append("--execute")
    result = _run(args, level, tmp_path / "daemon.log")
    assert result.returncode == 0, result.stderr
    expected = {"dry_run": not execute, "failures": ["offline maintenance failure"]}
    if command == "gc":
        expected = {
            key: expected
            for key in ("prune_orphans", "gc_worktree_meta", "prune_symbols", "optimize_caches")
        }
    assert json.loads(result.stdout) == expected
    events = [json.loads(line)["event"] for line in result.stderr.splitlines()]
    expected_events = {
        "DEBUG": [
            "maintenance_debug",
            "maintenance_info",
            "maintenance_warning",
            "maintenance_error",
            "maintenance_stdlib_warning",
        ],
        "INFO": [
            "maintenance_info",
            "maintenance_warning",
            "maintenance_error",
            "maintenance_stdlib_warning",
        ],
        "ERROR": ["maintenance_error"],
    }
    assert events == expected_events[level] * (4 if command == "gc" else 1)
    assert not (tmp_path / "daemon.log").exists()
    assert not (tmp_path / "daemon.log.lock").exists()


@pytest.mark.parametrize("command", ["gc", "prune-orphans", "prune-symbols"])
def test_real_maintenance_refusals_leave_json_and_protected_files(tmp_path, command):
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
from lgrep.cli import main

root = Path(sys.argv.pop())
orphan = {'path': str(root / 'protected'), 'reason': 'missing_meta',
          'bytes': 8, 'project_path': None}
stale = {'path': str(root / 'protected' / 'index_protected.json'),
         'reason': 'repo_path_enoent', 'bytes': 8, 'repo_path': None}
with (
    patch('lgrep.tools.prune_orphans.find_orphans', return_value=[orphan]),
    patch('lgrep.tools.prune_symbols.find_stale_indexes', return_value=[stale]),
):
    sys.exit(main())
"""
    cache, symbols = tmp_path / "cache", tmp_path / "symbols"
    cache.mkdir()
    symbols.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    sentinel = protected / "index_protected.json"
    sentinel.write_text("preserve")
    args = ["-c", script, command, "--execute"]
    if command == "prune-symbols":
        args.extend(["--storage-dir", str(symbols)])
    else:
        args.extend(["--cache-dir", str(cache)])
    if command == "gc":
        args.extend(["--symbols-dir", str(symbols)])
    args.append(str(tmp_path))
    result = _run(args, "INFO", tmp_path / "daemon.log")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    reports = [report["prune_orphans"], report["prune_symbols"]] if command == "gc" else [report]
    for entry in reports:
        assert entry["dry_run"] is False
        assert len(entry["failures"]) == 1
        assert entry["failures"][0]["error"].startswith("refused: path is outside")
        assert entry["reclaimed_bytes"] == 0
        assert entry.get("deleted_dirs", entry.get("deleted_files")) == 0
    events = [json.loads(line) for line in result.stderr.splitlines()]
    assert len(events) == (2 if command == "gc" else 1)
    assert all(event["event"] == "prune_refused_outside_root" for event in events)
    assert all(event["level"] == "warning" for event in events)
    assert sentinel.read_text() == "preserve"
    assert not (tmp_path / "daemon.log").exists()
    assert not (tmp_path / "daemon.log.lock").exists()
