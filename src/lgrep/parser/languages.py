"""Language specification registry for the lgrep symbol parser.

Maps file extensions to tree-sitter language names and symbol query patterns.
Uses tree-sitter-language-pack for pre-built parsers (165+ languages).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LanguageSpec:
    """Specification for a supported language.

    Attributes:
        name: tree-sitter language name (used with get_parser(name))
        extensions: File extensions that map to this language
        function_kinds: tree-sitter node types for function-like symbols
        class_kinds: tree-sitter node types for class-like symbols
        method_kinds: tree-sitter node types for method-like symbols
        interface_kinds: tree-sitter node types for interface-like symbols
    """

    name: str
    extensions: list[str]
    function_kinds: list[str] = field(default_factory=list)
    class_kinds: list[str] = field(default_factory=list)
    method_kinds: list[str] = field(default_factory=list)
    interface_kinds: list[str] = field(default_factory=list)


# Registry of supported languages
_LANGUAGE_SPECS: list[LanguageSpec] = [
    LanguageSpec(
        name="python",
        extensions=[".py", ".pyi"],
        function_kinds=["function_definition"],
        class_kinds=["class_definition"],
        method_kinds=["function_definition"],  # methods are function_definition inside class
    ),
    LanguageSpec(
        name="javascript",
        extensions=[".js", ".mjs", ".cjs"],
        function_kinds=[
            "function_declaration",
            "function",
            "arrow_function",
            "generator_function_declaration",
        ],
        class_kinds=["class_declaration", "class"],
        method_kinds=["method_definition"],
    ),
    LanguageSpec(
        name="typescript",
        extensions=[".ts", ".mts", ".cts"],
        function_kinds=[
            "function_declaration",
            "function",
            "arrow_function",
            "generator_function_declaration",
        ],
        class_kinds=["class_declaration", "class"],
        method_kinds=["method_definition"],
        interface_kinds=["interface_declaration"],
    ),
    LanguageSpec(
        name="tsx",
        extensions=[".tsx"],
        function_kinds=[
            "function_declaration",
            "function",
            "arrow_function",
        ],
        class_kinds=["class_declaration", "class"],
        method_kinds=["method_definition"],
        interface_kinds=["interface_declaration"],
    ),
    LanguageSpec(
        name="go",
        extensions=[".go"],
        function_kinds=["function_declaration", "method_declaration"],
        class_kinds=["type_declaration"],  # Go uses type declarations for structs
        method_kinds=["method_declaration"],
        interface_kinds=["type_declaration"],
    ),
    LanguageSpec(
        name="rust",
        extensions=[".rs"],
        function_kinds=["function_item"],
        class_kinds=["struct_item", "enum_item", "impl_item"],
        method_kinds=["function_item"],
        interface_kinds=["trait_item"],
    ),
    LanguageSpec(
        name="java",
        extensions=[".java"],
        function_kinds=["method_declaration", "constructor_declaration"],
        class_kinds=["class_declaration", "enum_declaration", "record_declaration"],
        method_kinds=["method_declaration"],
        interface_kinds=["interface_declaration"],
    ),
    LanguageSpec(
        name="c",
        extensions=[".c", ".h"],
        function_kinds=["function_definition"],
        class_kinds=["struct_specifier", "union_specifier", "enum_specifier"],
        method_kinds=[],
    ),
    LanguageSpec(
        name="c_sharp",
        extensions=[".cs"],
        function_kinds=["method_declaration", "constructor_declaration"],
        class_kinds=["class_declaration", "struct_declaration", "record_declaration"],
        method_kinds=["method_declaration"],
        interface_kinds=["interface_declaration"],
    ),
    LanguageSpec(
        name="cpp",
        extensions=[".cpp", ".cc", ".cxx", ".hpp", ".hh"],
        function_kinds=["function_definition"],
        class_kinds=["class_specifier", "struct_specifier"],
        method_kinds=["function_definition"],
    ),
    LanguageSpec(
        name="ruby",
        extensions=[".rb"],
        function_kinds=["method"],
        class_kinds=["class"],
        method_kinds=["method"],
        interface_kinds=["module"],
    ),
    LanguageSpec(
        name="php",
        extensions=[".php"],
        function_kinds=["function_definition"],
        class_kinds=["class_declaration"],
        method_kinds=["method_declaration"],
        interface_kinds=["interface_declaration"],
    ),
    LanguageSpec(
        name="swift",
        extensions=[".swift"],
        function_kinds=["function_declaration"],
        class_kinds=["class_declaration", "struct_declaration"],
        method_kinds=["function_declaration"],
        interface_kinds=["protocol_declaration"],
    ),
    LanguageSpec(
        name="kotlin",
        extensions=[".kt", ".kts"],
        function_kinds=["function_declaration"],
        class_kinds=["class_declaration", "object_declaration"],
        method_kinds=["function_declaration"],
        interface_kinds=["interface_declaration"],
    ),
]

# Build lookup dict: extension → LanguageSpec
_EXT_TO_SPEC: dict[str, LanguageSpec] = {}
for _spec in _LANGUAGE_SPECS:
    for _ext in _spec.extensions:
        _EXT_TO_SPEC[_ext] = _spec


def get_language_spec(extension: str) -> LanguageSpec | None:
    """Look up the LanguageSpec for a file extension.

    Args:
        extension: File extension including the dot (e.g. ".py", ".ts")

    Returns:
        LanguageSpec if the extension is supported, None otherwise
    """
    return _EXT_TO_SPEC.get(extension.lower())


def supported_extensions() -> list[str]:
    """Return all supported file extensions."""
    return list(_EXT_TO_SPEC.keys())
