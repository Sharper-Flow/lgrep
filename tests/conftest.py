"""Suite-wide fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_gc_stores(tmp_path, monkeypatch):
    """Pin both maintenance store roots to this test's temporary storage.

    ``lgrep gc --execute`` tests reach real ``prune_symbols``,
    ``gc_worktree_meta``, and ``optimize_caches`` passes. Without this
    fixture the symbol store falls back to the developer's
    ``~/.cache/lgrep/symbols`` when ``LGREP_SYMBOLS_DIR`` is unset, and
    inherited ``LGREP_CACHE_DIR``/``LGREP_SYMBOLS_DIR`` values escape
    the per-test fixture entirely.
    """
    monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path / "symbols"))


@pytest.fixture
def cli_logging(capsys):
    """Release CLI handlers before the test's temporary stderr stream closes."""
    import logging

    import structlog

    from lgrep.logging_setup import shutdown_logging

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    config = structlog.get_config().copy()
    try:
        yield
    finally:
        shutdown_logging()
        root.handlers[:] = handlers
        root.setLevel(level)
        structlog.configure(**config)


@pytest.fixture(autouse=True)
def _restore_tool_registry():
    """Restore the shared server's tool registry after each test.

    ``run_server`` applies the tool profile by unregistering tools from the
    process-wide ``mcp`` singleton; later tests expect the full registry.
    """
    from lgrep.server import mcp

    tools = mcp._tool_manager._tools
    saved = dict(tools)
    yield
    tools.clear()
    tools.update(saved)
