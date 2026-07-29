"""Symbol and occurrence dataclasses and stable ID generation for lgrep.

Symbol IDs use the deterministic format: "file_path:kind:name"
Example: "src/auth.py:function:authenticate"

Occurrence IDs use the deterministic format: "file_path:occurrence:name:start_byte"
Example: "src/auth.py:occurrence:authenticate:42"

These formats are:
- Human-readable and debuggable
- Stable for unchanged symbols (same file, kind, name → same ID)
- Invalidated correctly on rename (which is the right signal)
- No line number in ID — name alone disambiguates within a file
"""

from __future__ import annotations

from dataclasses import dataclass, field


def make_symbol_id(file_path: str, kind: str, name: str, parent: str | None = None) -> str:
    """Generate a deterministic, stable symbol ID.

    Args:
        file_path: Relative or absolute path to the source file
        kind: Symbol kind (function, class, method, interface, etc.)
        name: Symbol name
        parent: Optional parent symbol name (e.g. class name for methods)

    Returns:
        Stable string ID in format "file_path:kind:name".
        Methods include parent context when available:
        "file_path:method:Parent.name"
    """
    if parent:
        return f"{file_path}:{kind}:{parent}.{name}"
    return f"{file_path}:{kind}:{name}"


def make_occurrence_id(file_path: str, name: str, start_byte: int) -> str:
    """Generate a deterministic occurrence ID.

    Args:
        file_path: Relative or absolute path to the source file
        name: Identifier text at this occurrence
        start_byte: Byte offset of the occurrence in the file

    Returns:
        Stable string ID in format "file_path:occurrence:name:start_byte".
    """
    return f"{file_path}:occurrence:{name}:{start_byte}"


@dataclass
class Symbol:
    """A code symbol extracted from a source file.

    Attributes:
        id: Stable deterministic ID (file_path:kind:name)
        name: Symbol name (e.g. "authenticate")
        kind: Symbol kind (function, class, method, interface, etc.)
        file_path: Path to the source file (relative to repo root when possible)
        start_byte: Byte offset of the symbol start in the file
        end_byte: Byte offset of the symbol end in the file
        docstring: Optional extracted docstring/JSDoc comment
        decorators: Optional list of decorator strings (e.g. ["@staticmethod"])
        parent: Optional parent symbol name (for methods inside classes)
    """

    id: str
    name: str
    kind: str
    file_path: str
    start_byte: int
    end_byte: int
    docstring: str | None = None
    decorators: list[str] | None = field(default_factory=list)
    parent: str | None = None


@dataclass
class Occurrence:
    """A candidate identifier occurrence extracted from a source file.

    Occurrences are candidate usage locations only. They are not resolved
    references and do not claim compiler-accurate or exhaustive results.

    Attributes:
        id: Stable deterministic ID (file_path:occurrence:name:start_byte)
        name: Identifier text at this occurrence
        file_path: Path to the source file (relative to repo root when possible)
        start_byte: Byte offset of the occurrence start in the file
        end_byte: Byte offset of the occurrence end in the file
        line_number: 1-based line number of the occurrence
        line_text: Full source line containing the occurrence
        kind: Occurrence kind (call, attribute, import, reference)
        enclosing_symbol_id: Optional ID of the nearest enclosing symbol
        is_test_file: Heuristic test-file classification for this occurrence
    """

    id: str
    name: str
    file_path: str
    start_byte: int
    end_byte: int
    line_number: int
    line_text: str
    kind: str
    enclosing_symbol_id: str | None = None
    is_test_file: bool = False
