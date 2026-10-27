"""Run the GC execute tests with guarded storage resolution in a fresh process."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_GUARD_PLUGIN = """
import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def gc_storage_guard(tmp_path, monkeypatch):
    seen = set()

    def guard(module_name, attribute, store):
        module = importlib.import_module(module_name)
        original = getattr(module, attribute)

        def checked(*args, **kwargs):
            root = Path(original(*args, **kwargs)).absolute()
            assert root.is_relative_to(tmp_path), (
                f"GC {store} root outside test fixture: {root}; fixture: {tmp_path}"
            )
            seen.add(store)
            return root

        monkeypatch.setattr(module, attribute, checked)

    guard("lgrep.tools.prune_orphans", "_resolve_cache_dir", "semantic")
    guard("lgrep.tools.prune_symbols", "_resolve_storage_dir", "symbol")
    yield
    assert seen == {"semantic", "symbol"}, f"Real GC passes not exercised: {seen}"
"""


@pytest.mark.parametrize("cache_environment", ["absent", "inherited"])
@pytest.mark.parametrize(
    "node_id",
    [
        "tests/test_cli.py::TestGcSubcommand::test_gc_execute",
        "tests/test_worktree_cache.py::TestGcWorktreeMeta::test_lgrep_gc_runs_both_passes",
    ],
    ids=["cli", "worktree"],
)
def test_execute_stores_stay_in_fixture(tmp_path, node_id, cache_environment):
    repo = Path(__file__).resolve().parents[2]
    plugin = tmp_path / "gc_isolation_guard.py"
    plugin.write_text(_GUARD_PLUGIN)
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    for key in (
        "LGREP_CACHE_DIR",
        "LGREP_SYMBOLS_DIR",
        "PYTEST_ADDOPTS",
        "VOYAGE_API_KEY",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        environment.pop(key, None)
    environment["HOME"] = str(home)
    environment["XDG_CACHE_HOME"] = str(tmp_path / "xdg")
    environment["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(repo / "src")])
    if cache_environment == "inherited":
        environment["LGREP_CACHE_DIR"] = str(tmp_path / "inherited-semantic")
        environment["LGREP_SYMBOLS_DIR"] = str(tmp_path / "inherited-symbols")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "pyproject.toml",
            "-p",
            "gc_isolation_guard",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "pytest"),
            "-q",
            node_id,
        ],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
