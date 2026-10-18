"""Source retrieval must agree with refreshed symbol ranges in linked worktrees."""

from __future__ import annotations

import os
import subprocess

import pytest

from lgrep.discovery import FileDiscovery
from lgrep.parser.extractor import SymbolExtractor
from lgrep.storage.index_store import IndexStore
from lgrep.tools.get_symbol import get_symbol, get_symbols
from lgrep.tools.index_folder import index_folder
from lgrep.tools.search_symbols import search_symbols


def _git(root, *args):
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )


@pytest.fixture
def linked_typescript(tmp_path):
    trunk = tmp_path / "trunk"
    trunk.mkdir()
    prefix = "// padding inside a large TypeScript source file\n" * 1200
    body = "function samePath(a: string, b: string): boolean {\n  return a === b;\n}"
    (trunk / "large.ts").write_text(prefix + body + "\n", encoding="utf-8")
    _git(trunk, "init")
    _git(trunk, "add", ".")
    _git(trunk, "commit", "-m", "fixture")
    worktree = tmp_path / "linked"
    _git(trunk, "worktree", "add", "-b", "fixture", str(worktree))
    return trunk, worktree, prefix, body


@pytest.mark.parametrize("padding", ["// shifted\n" * 10, "// café 雪 🙂\n" * 10])
@pytest.mark.parametrize("seeded", [False, True])
def test_refreshed_lookup_returns_shifted_typescript_body(
    linked_typescript, tmp_path, padding, seeded
):
    trunk, worktree, prefix, body = linked_typescript
    store_dir = tmp_path / "store"
    indexed = trunk if seeded else worktree
    assert "error" not in index_folder(str(indexed), storage_dir=store_dir)
    target = worktree / "large.ts"
    target.write_text(padding + prefix + body + "\n", encoding="utf-8")
    indexed_at = IndexStore(store_dir).last_indexed_at(str(indexed))
    os.utime(target, (indexed_at + 1, indexed_at + 1))

    found = search_symbols("samePath", str(worktree), storage_dir=store_dir)
    assert found["index_refreshed"] is True
    assert len(found["results"]) == 1
    symbol = found["results"][0]
    assert symbol["start_byte"] > 47000
    result = get_symbol(symbol["id"], str(worktree), storage_dir=store_dir)
    assert "error" not in result
    assert result["symbol"]["source"] == body


def test_batch_validates_one_snapshot_per_file(linked_typescript, tmp_path, monkeypatch):
    _, worktree, prefix, body = linked_typescript
    store_dir = tmp_path / "store"
    target = worktree / "large.ts"
    other_body = "function other(): number { return 42; }"
    target.write_text(prefix + body + "\n" + other_body, encoding="utf-8")
    assert "error" not in index_folder(str(worktree), storage_dir=store_dir)
    extract = SymbolExtractor.extract
    snapshots = []

    def extract_then_edit(self, file_path, repo_root=None, *, source=None):
        snapshots.append(source)
        result = extract(self, file_path, repo_root, source=source)
        target.write_text("// changed after snapshot was read\n", encoding="utf-8")
        return result

    monkeypatch.setattr(SymbolExtractor, "extract", extract_then_edit)
    result = get_symbols(
        ["large.ts:function:samePath", "large.ts:function:other"],
        str(worktree),
        storage_dir=store_dir,
    )
    assert [symbol["source"] for symbol in result["symbols"]] == [body, other_body]
    assert len(snapshots) == 1


def test_github_lookup_refuses_changed_symbol(linked_typescript, tmp_path, monkeypatch):
    import httpx

    _, worktree, prefix, body = linked_typescript
    store_dir = tmp_path / "store"
    assert "error" not in index_folder(str(worktree), storage_dir=store_dir)
    store = IndexStore(store_dir)
    index = store.load(str(worktree))
    index.repo_path = "github:owner/repo@main"
    store.save(index)
    response = httpx.Response(
        200,
        content=(prefix + body.replace("samePath", "otherFun")).encode(),
        request=httpx.Request("GET", "https://example.invalid/large.ts"),
    )
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: response)
    result = get_symbol("large.ts:function:samePath", index.repo_path, storage_dir=store_dir)
    assert "error" in result
    assert "symbol" not in result


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("mismatch", ["shift", "other_symbol", "truncated", "negative"])
def test_source_lookup_refuses_mismatched_range(linked_typescript, tmp_path, batch, mismatch):
    _, worktree, prefix, body = linked_typescript
    store_dir = tmp_path / "store"
    assert "error" not in index_folder(str(worktree), storage_dir=store_dir)
    target = worktree / "large.ts"
    symbol_id = "large.ts:function:samePath"
    if mismatch == "shift":
        target.write_text("// shift\n" * 10 + prefix + body + "\n", encoding="utf-8")
    elif mismatch == "other_symbol":
        target.write_text(prefix + body.replace("samePath", "otherFun") + "\n", encoding="utf-8")
    else:
        store = IndexStore(store_dir)
        index = store.load(str(worktree))
        symbol = index.symbols[symbol_id]
        if mismatch == "truncated":
            symbol["end_byte"] += 100
        else:
            symbol["start_byte"] = -1
        store.save(index)

    if batch:
        result = get_symbols([symbol_id], str(worktree), storage_dir=store_dir)["symbols"][0]
        assert "source" not in result
    else:
        result = get_symbol(symbol_id, str(worktree), storage_dir=store_dir)
        assert "symbol" not in result
    assert "error" in result


def test_refresh_updates_indexed_file_beyond_default_walk_cap(
    linked_typescript, tmp_path, monkeypatch
):
    _, worktree, prefix, body = linked_typescript
    store_dir = tmp_path / "store"
    fillers = [worktree / f"filler{i:03d}.ts" for i in range(500)]
    for filler in fillers:
        filler.write_text("// unchanged\n", encoding="utf-8")
    target = worktree / "large.ts"
    # Fix traversal order so the already-indexed target falls outside the
    # default refresh window regardless of filesystem directory ordering.
    monkeypatch.setattr(FileDiscovery, "find_files", lambda self: iter([*fillers, target]))
    assert "error" not in index_folder(str(worktree), storage_dir=store_dir, max_files=501)
    target.write_text("// café 雪 🙂\n" * 10 + prefix + body + "\n", encoding="utf-8")
    indexed_at = IndexStore(store_dir).last_indexed_at(str(worktree))
    os.utime(target, (indexed_at + 1, indexed_at + 1))

    found = search_symbols("samePath", str(worktree), storage_dir=store_dir)
    assert found["index_refreshed"] is True
    assert len(found["results"]) == 1
    symbol = found["results"][0]
    result = get_symbol(symbol["id"], str(worktree), storage_dir=store_dir)
    assert "error" not in result
    assert result["symbol"]["source"] == body


def test_incremental_parse_budget_remains_bounded(linked_typescript, tmp_path, monkeypatch):
    _, worktree, _, _ = linked_typescript
    store_dir = tmp_path / "store"
    target = worktree / "large.ts"
    assert "error" not in index_folder(str(worktree), storage_dir=store_dir)
    new_files = [worktree / f"new{i}.ts" for i in range(3)]
    for new_file in new_files:
        new_file.write_text("function added() {}", encoding="utf-8")
    monkeypatch.setattr(FileDiscovery, "find_files", lambda self: iter([target, *new_files]))

    result = index_folder(str(worktree), storage_dir=store_dir, max_files=2)
    assert result["files_skipped"] == 1
    assert result["files_indexed"] == 2
    index = IndexStore(store_dir).load(str(worktree))
    assert index.walk_truncated is True
    assert "new2.ts" not in index.files
