"""Canonical tool argument names, legacy synonyms, and refusal text.

One module owns the synonym table and the refusal messages used by the
``LgrepFastMCP.call_tool`` seam. The seam runs before FastMCP validates a
call, so every tool schema declares exactly one name per concept while
well-known legacy spellings still reach the canonical argument instead of
being silently dropped during validation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from mcp.types import ContentBlock

# Legacy spelling -> canonical argument name, accepted on any tool that
# declares the canonical name. Synonyms never appear in a tool schema, so
# each concept keeps one declared name:
#   limit    - result cap
#   query    - search text or symbol name
#   path     - local repository root or file
# max_files is a separate concept: it counts files scanned, not results.
SYNONYMS: dict[str, str] = {
    "max_results": "limit",
    "maxResults": "limit",
    "symbol": "query",
    "symbol_name": "query",
    "file_path": "path",
    "file": "path",
    "folder": "path",
}

# Synonyms accepted from a single tool only: (tool, legacy) -> canonical.
TOOL_SYNONYMS: dict[tuple[str, str], str] = {
    ("search_text", "pattern"): "query",
}

# (tool, argument) -> tool that declares the argument. The refusal names the
# owning tool so the caller reroutes instead of retrying the wrong tool with
# renamed arguments.
WRONG_TOOL_HINTS: dict[tuple[str, str], str] = {
    ("search_references", "symbol_id"): "get_symbol",
    ("index_symbols_repo", "path"): "index_symbols_folder",
}


def _canonical_name(tool_name: str, argument: str) -> str | None:
    """Return the canonical name a legacy spelling maps to for one tool."""
    scoped = TOOL_SYNONYMS.get((tool_name, argument))
    if scoped is not None:
        return scoped
    return SYNONYMS.get(argument)


def refusal_message(tool_name: str, argument: str, declared: Iterable[str]) -> str:
    """Build the refusal text for an undeclared argument.

    The message names the tool, the rejected argument, and the arguments the
    tool declares. A wrong-tool call also names the tool that declares the
    argument.
    """
    message = f"Unknown argument '{argument}' for tool '{tool_name}'."
    hint = WRONG_TOOL_HINTS.get((tool_name, argument))
    if hint is not None:
        message += f" Argument '{argument}' belongs to tool '{hint}'."
    valid = ", ".join(sorted(declared)) or "(none)"
    message += f" Valid arguments for '{tool_name}': {valid}."
    return message


def normalize_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Rename accepted synonyms to canonical names and refuse anything else.

    ``schema`` is the tool's declared JSON Schema. A synonym applies only
    when the tool declares the canonical name and the caller did not send
    both spellings. Any remaining undeclared argument raises ToolError. The
    caller's dict is not mutated.
    """
    declared: set[str] = set((schema or {}).get("properties", {}))
    normalized = dict(arguments)
    for key in list(normalized):
        canonical = _canonical_name(tool_name, key)
        if canonical is not None and canonical in declared and canonical not in normalized:
            normalized[canonical] = normalized.pop(key)
    for key in normalized:
        if key not in declared:
            raise ToolError(refusal_message(tool_name, key, declared))
    return normalized


class LgrepFastMCP(FastMCP):
    """FastMCP whose public call_tool normalizes arguments before validation."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Rename synonyms and refuse undeclared arguments, then call the tool."""
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            arguments = normalize_tool_arguments(name, arguments, tool.parameters)
        return await super().call_tool(name, arguments)
