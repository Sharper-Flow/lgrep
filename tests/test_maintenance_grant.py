"""Destructive MCP maintenance requires an explicit capability grant.

Regression cover for rq-destructiveGrant01. The previous guard trusted a
transport reported as ``stdio``, on the assumption that stdio implies a
single local caller. Vision breaks that assumption: it runs lgrep as a stdio
subprocess and republishes it on a shared, unauthenticated HTTP port, so a
proxied client inherited full destructive rights.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lgrep.server import mcp
from lgrep.storage import get_project_db_path, write_project_meta
from lgrep.storage.index_store import IndexStore
from lgrep.tools.index_folder import index_folder

if TYPE_CHECKING:
    from pathlib import Path

GRANT_ENV = "LGREP_ALLOW_DESTRUCTIVE_MCP"
DESTRUCTIVE_TOOLS = ("prune_orphans", "prune_symbols")


def _hash_name(label: str) -> str:
    """Produce a 12-hex cache-dir name deterministically from a label."""
    return hashlib.sha256(label.encode()).hexdigest()[:12]


def _make_orphan_cache(cache_root: Path, label: str) -> Path:
    """Create a cache directory that ``prune_orphans`` will classify as an orphan."""
    cache_dir = cache_root / _hash_name(label)
    cache_dir.mkdir(parents=True)
    (cache_dir / "chunks.lance").mkdir()
    return cache_dir


def _key(label: str) -> str:
    """Produce a 16-hex index-file suffix deterministically from a label."""
    return hashlib.sha256(label.encode()).hexdigest()[:16]


def _make_stale_symbol_index(storage_root: Path, label: str) -> Path:
    """Create a symbol index whose ``repo_path`` no longer exists."""
    storage_root.mkdir(parents=True, exist_ok=True)
    index_file = storage_root / f"index_{_key(label)}.json"
    missing_repo = storage_root.parent / f"gone-repo-{label}"
    index_file.write_text(
        json.dumps({"files": {}, "symbols": {}, "version": "2.0", "repo_path": str(missing_repo)}),
        encoding="utf-8",
    )
    return index_file


def _tool_fn(name: str):
    for tool in mcp._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    raise KeyError(f"Tool not found: {name}")


class _InlineRuntime:
    """Runs supervised work inline so the guard, not the scheduler, is under test."""

    async def run_blocking(self, kind, caller, project, fn, *args, **kwargs):
        kwargs.pop("cancel_event", None)
        return fn(*args, **kwargs)


def _stdio_ctx():
    """A context that reports the transport as local stdio.

    This is exactly what lgrep reports when Vision proxies it, which is why
    transport must not decide authority.
    """
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                projects={}, runtime=_InlineRuntime(), transport="stdio"
            )
        )
    )


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_destructive_refused_without_grant_even_on_stdio(tool_name, monkeypatch):
    monkeypatch.delenv(GRANT_ENV, raising=False)

    result = await _tool_fn(tool_name)(dry_run=False, ctx=_stdio_ctx())

    assert result["dry_run"] is True, (
        f"{tool_name} performed a destructive run without the {GRANT_ENV} grant. "
        "A stdio transport behind a shared proxy must not confer destructive rights."
    )
    assert result["deleted_dirs" if tool_name == "prune_orphans" else "deleted_files"] == 0

    reason = result.get("refused_reason")
    assert reason, f"{tool_name} must report why the destructive run was refused"
    assert GRANT_ENV in reason, "refusal must name the required grant"
    assert tool_name.replace("_", "-") in reason, "refusal must name the CLI equivalent"


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_destructive_allowed_with_grant_deletes_fixtures(tool_name, monkeypatch, tmp_path):
    monkeypatch.setenv(GRANT_ENV, "1")
    monkeypatch.setenv("LGREP_PRUNE_MIN_AGE_S", "0")

    if tool_name == "prune_orphans":
        cache_root = tmp_path / "cache"
        monkeypatch.setenv("LGREP_CACHE_DIR", str(cache_root))
        fixture = _make_orphan_cache(cache_root, "orphan-mcp-granted")
    else:
        symbols_root = tmp_path / "symbols"
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(symbols_root))
        fixture = _make_stale_symbol_index(symbols_root, "stale-mcp-granted")

    result = await _tool_fn(tool_name)(dry_run=False, ctx=_stdio_ctx())

    assert result["dry_run"] is False, (
        f"{tool_name} must honour the caller's destructive request once {GRANT_ENV} is set"
    )
    assert result.get("refused_reason") == ""

    if tool_name == "prune_orphans":
        assert result["deleted_dirs"] >= 1
        assert not fixture.exists()
    else:
        assert result["deleted_files"] >= 1
        assert not fixture.exists()


@pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
@pytest.mark.asyncio
async def test_preview_request_is_never_marked_refused(tool_name, monkeypatch):
    """A caller that asked for a preview was not refused anything."""
    monkeypatch.delenv(GRANT_ENV, raising=False)

    result = await _tool_fn(tool_name)(dry_run=True, ctx=_stdio_ctx())

    assert result["dry_run"] is True
    assert result.get("refused_reason") == ""


def test_transport_no_longer_decides_authority():
    """The transport-trust helper must be gone, not merely bypassed."""
    from lgrep.server import tools_maintenance

    assert not hasattr(tools_maintenance, "_transport_is_local"), (
        "transport-based trust inference must be removed; a proxy can front a "
        "local stdio pipe with a shared network surface"
    )


class TestStructuralDestructiveGrantCoverage:
    """Registry-wide grant behaviour without cross-tool wording assertions.

    Each destructive MCP tool is covered structurally: refusal names the grant
    and, where applicable, the CLI equivalent; the grant removes refusal and
    allows the destructive operation. The exact response shape is
    tool-specific, so assertions are not shared across tools with different
    contracts.
    """

    def _tool_fn(self, name: str):
        for tool in mcp._tool_manager.list_tools():
            if tool.name == name:
                return tool.fn
        raise KeyError(f"Tool not found: {name}")

    def _ctx(self):
        return SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context=SimpleNamespace(
                    projects={}, runtime=_InlineRuntime(), transport="stdio"
                )
            )
        )

    def test_all_destructive_tools_are_registry_pinned(self):
        destructive = {
            t.name
            for t in mcp._tool_manager.list_tools()
            if t.annotations is not None and t.annotations.destructiveHint is True
        }
        assert destructive == {
            "prune_orphans",
            "prune_symbols",
            "invalidate_cache",
            "invalidate_worktree_cache",
        }, (
            f"Destructive registry population changed: {destructive}. "
            "Update pinned tools and grant coverage."
        )

    @pytest.mark.parametrize("tool_name", ["prune_orphans", "prune_symbols"])
    @pytest.mark.asyncio
    async def test_prune_tools_refusal_names_grant_and_cli(self, tool_name, monkeypatch, tmp_path):
        monkeypatch.delenv(GRANT_ENV, raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("LGREP_SYMBOLS_DIR", str(tmp_path / "symbols"))

        result = await self._tool_fn(tool_name)(dry_run=False, ctx=self._ctx())

        assert result.get("refused_reason")
        assert GRANT_ENV in result["refused_reason"]
        assert tool_name.replace("_", "-") in result["refused_reason"]
        assert "--execute" in result["refused_reason"]

    @pytest.mark.asyncio
    async def test_invalidate_cache_refusal_names_grant_and_no_cli(self, monkeypatch, tmp_path):
        monkeypatch.delenv(GRANT_ENV, raising=False)
        monkeypatch.setattr("lgrep.storage.index_store.DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
        (tmp_path / "repo").mkdir()

        result = await self._tool_fn("invalidate_cache")(path=str(tmp_path / "repo"))

        assert result.get("refused_reason")
        assert GRANT_ENV in result["refused_reason"]
        assert "no cli equivalent" in result["refused_reason"].lower()
        assert "--execute" not in result["refused_reason"]

    @pytest.mark.asyncio
    async def test_invalidate_worktree_cache_refusal_names_grant_and_no_cli(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.delenv(GRANT_ENV, raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path)

        result = await self._tool_fn("invalidate_worktree_cache")(
            paths=[str(project)], ctx=self._ctx()
        )

        assert result.get("refused_reason")
        assert GRANT_ENV in result["refused_reason"]
        assert "no cli equivalent" in result["refused_reason"].lower()
        assert "--execute" not in result["refused_reason"]


class TestRemainingDestructiveGrants:
    """Grant gating for the remaining destructive MCP handlers.

    ``invalidate_cache`` and ``invalidate_worktree_cache`` delete persistent
    storage, so they refuse destructive runs unless the server carries the
    explicit ``LGREP_ALLOW_DESTRUCTIVE_MCP`` grant.
    """

    def _tool_fn(self, name: str):
        for tool in mcp._tool_manager.list_tools():
            if tool.name == name:
                return tool.fn
        raise KeyError(f"Tool not found: {name}")

    @pytest.mark.asyncio
    async def test_invalidate_cache_refused_without_grant(self, tmp_path, monkeypatch):
        monkeypatch.delenv(GRANT_ENV, raising=False)
        monkeypatch.setattr("lgrep.storage.index_store.DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "file.py").write_text("def f(): pass\n")
        index_folder(str(repo), storage_dir=tmp_path / "symbols")
        index_file = IndexStore(tmp_path / "symbols")._index_path(str(repo))
        assert index_file.is_file()

        result = await self._tool_fn("invalidate_cache")(path=str(repo))

        assert result["status"] == "refused", (
            "invalidate_cache must refuse destructive runs without the grant"
        )
        reason = result.get("refused_reason")
        assert reason, "refusal must report why"
        assert GRANT_ENV in reason, "refusal must name the required grant"
        assert "no cli equivalent" in reason.lower(), (
            "refusal must truthfully say no CLI equivalent"
        )
        # The fixture's existing symbol-store entry was not touched.
        assert index_file.is_file()

    @pytest.mark.asyncio
    async def test_invalidate_cache_honoured_with_grant(self, tmp_path, monkeypatch):
        monkeypatch.setenv(GRANT_ENV, "1")
        monkeypatch.setattr("lgrep.storage.index_store.DEFAULT_SYMBOLS_DIR", tmp_path / "symbols")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "file.py").write_text("def f(): pass\n")
        index_folder(str(repo), storage_dir=tmp_path / "symbols")
        index_file = IndexStore(tmp_path / "symbols")._index_path(str(repo))
        assert index_file.is_file()

        result = await self._tool_fn("invalidate_cache")(path=str(repo))

        assert result["status"] == "deleted", (
            "invalidate_cache must honour the destructive request once the grant is set"
        )
        assert result.get("refused_reason") == ""
        from lgrep.tools.list_repos import list_repos

        assert str(repo) not in list_repos(storage_dir=tmp_path / "symbols")["repos"]
        assert not index_file.exists()

    @pytest.mark.asyncio
    async def test_invalidate_worktree_cache_refused_without_grant(self, tmp_path, monkeypatch):
        monkeypatch.delenv(GRANT_ENV, raising=False)
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path)
        (db_path / "chunks.lance").mkdir(parents=True)

        result = await self._tool_fn("invalidate_worktree_cache")(paths=[str(project)])

        assert result["paths_cleaned"] == 0, (
            "invalidate_worktree_cache must not clean anything without the grant"
        )
        assert result["bytes_reclaimed"] == 0
        assert result["entries"] == []
        reason = result.get("refused_reason")
        assert reason, "refusal must report why"
        assert GRANT_ENV in reason, "refusal must name the required grant"
        assert "no cli equivalent" in reason.lower(), (
            "refusal must truthfully say no CLI equivalent"
        )
        # No live cache was touched
        assert db_path.is_dir()

    @pytest.mark.asyncio
    async def test_invalidate_worktree_cache_honoured_with_grant(self, tmp_path, monkeypatch):
        monkeypatch.setenv(GRANT_ENV, "1")
        monkeypatch.setenv("LGREP_CACHE_DIR", str(tmp_path / "cache"))
        project = tmp_path / "project"
        project.mkdir()
        db_path = get_project_db_path(project)
        write_project_meta(project, db_path=db_path)
        (db_path / "chunks.lance").mkdir(parents=True)

        # Remove the canonical project so the cache becomes an orphan fixture.
        project.rmdir()
        assert not project.exists()

        result = await self._tool_fn("invalidate_worktree_cache")(paths=[str(project)])

        assert result["paths_cleaned"] == 1, (
            "invalidate_worktree_cache must honour the destructive request once the grant is set"
        )
        assert result.get("refused_reason") == ""
        entry = result["entries"][0]
        assert entry["error"] is None
        assert entry["cache_deleted"] is True
        assert entry["bytes_reclaimed"] > 0
        assert not db_path.exists()
