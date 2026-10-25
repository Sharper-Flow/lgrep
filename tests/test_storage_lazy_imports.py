"""The symbol engine must import without the semantic storage libraries.

The symbol worker process imports only the symbol index store. If importing it
pulls in lancedb and pyarrow, every worker start pays seconds of import time and
holds about 130 MB of memory it never uses.
"""

import json
import subprocess
import sys

import pytest

import lgrep.storage
from lgrep.storage import _chunk_store

# Modules the worker imports outside its _QUERIES table, in its _op_* handlers.
_WORKER_HANDLER_MODULES = (
    "lgrep.tools.search_symbols",
    "lgrep.tools.index_folder",
    "lgrep.tools.index_repo",
)

_PROBE = """
import importlib, json, sys
import lgrep.symbol_worker as worker
modules = sorted({module for module, _ in worker._QUERIES.values()} | set(sys.argv[1:]))
for module in modules:
    importlib.import_module(module)
import lgrep.storage.index_store
loaded = [m for m in ("lancedb", "pyarrow") if m in sys.modules]
print(json.dumps({"modules": modules, "loaded": loaded}))
"""


def test_symbol_worker_and_tools_import_without_semantic_libraries():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *_WORKER_HANDLER_MODULES],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert "lgrep.tools.get_file_outline" in report["modules"]
    assert report["loaded"] == []


def test_every_reexport_resolves_to_the_chunk_store_object():
    assert lgrep.storage.__all__
    for name in lgrep.storage.__all__:
        assert getattr(lgrep.storage, name) is getattr(_chunk_store, name), name
        assert name in dir(lgrep.storage)


def test_unknown_attribute_still_raises():
    with pytest.raises(AttributeError, match="no_such_name"):
        lgrep.storage.no_such_name  # noqa: B018
