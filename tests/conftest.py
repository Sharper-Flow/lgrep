"""Suite-wide fixtures."""

from __future__ import annotations

import pytest


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
