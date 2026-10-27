"""C# uses the installed parser pack through local and remote indexing."""

import httpx
from tree_sitter_language_pack import get_parser

from lgrep.parser.extractor import SymbolExtractor
from lgrep.parser.languages import get_language_spec
from lgrep.storage.index_store import CodeIndex, IndexStore
from lgrep.tools.index_folder import index_folder
from lgrep.tools.index_repo import index_repo
from lgrep.tools.search_symbols import search_symbols

CSHARP_SOURCE = b'public class Greeter { public string Greet() { return "hello"; } }\n'


def test_csharp_registry_resolves_an_installed_parser():
    spec = get_language_spec(".cs")
    tree = get_parser(spec.name).parse(CSHARP_SOURCE)
    assert not tree.root_node.has_error


def test_csharp_symbols_are_extracted_and_locally_searchable(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "Greeter.cs"
    source.write_bytes(CSHARP_SOURCE)
    symbols = SymbolExtractor().extract(source, repo_root=root)
    assert {(symbol.name, symbol.kind) for symbol in symbols} == {
        ("Greeter", "class"),
        ("Greet", "method"),
    }
    for symbol in symbols:
        assert symbol.name.encode() in CSHARP_SOURCE[symbol.start_byte : symbol.end_byte]

    storage = tmp_path / "symbols"
    result = index_folder(str(root), storage_dir=storage)
    assert result["symbols_indexed"] == 2
    assert (
        search_symbols("Greet", str(root), storage_dir=storage, kind="method")["total_matches"] == 1
    )


async def test_python_and_csharp_remote_refresh_replaces_complete_baseline(tmp_path, monkeypatch):
    key = "github:owner/repo@main"
    store = IndexStore(tmp_path / "symbols")
    store.save(CodeIndex(repo_path=key, files={"old.py": "old-hash"}, symbols={}))
    client = httpx.AsyncClient
    contents = {"Greeter.cs": CSHARP_SOURCE, "helper.py": b"def python_helper(): pass\n"}

    def response(request):
        if "git/trees" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "tree": [{"type": "blob", "path": path} for path in contents],
                    "truncated": False,
                },
            )
        path = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=contents[path])

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(response))
    )
    result = await index_repo("owner/repo", ref="main", storage_dir=tmp_path / "symbols")

    assert result["published"] is True
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert result["files_indexed"] == 2
    assert result["symbols_indexed"] == 3
    saved = store.load(key)
    assert saved.walk_truncated is False
    assert set(saved.files) == set(contents)
    assert {symbol["name"] for symbol in saved.symbols.values()} == {
        "Greeter",
        "Greet",
        "python_helper",
    }
