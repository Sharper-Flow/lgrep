"""Symbol extractor using tree-sitter AST parsing.

Walks the AST of a source file and extracts Symbol objects for each
function, class, method, and interface definition.

Uses tree-sitter-language-pack for pre-built parsers (165+ languages).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lgrep.parser.languages import LanguageSpec, get_language_spec
from lgrep.parser.symbols import Symbol, make_symbol_id

log = structlog.get_logger()


def _get_node_name(node, source: bytes) -> str | None:
    """Extract the name from a named node (function/class/method definition)."""
    # Try common name child node types
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    return None


def _get_docstring_python(node, source: bytes) -> str | None:
    """Extract Python docstring from the first statement in a function/class body."""
    # Find the body node
    body = None
    for child in node.children:
        if child.type == "block":
            body = child
            break

    if body is None:
        return None

    # First non-trivial child of block is the docstring candidate
    for child in body.children:
        if child.type in ("newline", "indent", "comment"):
            continue

        # Direct string node (tree-sitter 0.21+ style)
        if child.type == "string":
            raw = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return _strip_string_quotes(raw)

        # expression_statement wrapping a string (older tree-sitter style)
        if child.type == "expression_statement":
            for grandchild in child.children:
                if grandchild.type == "string":
                    raw = source[grandchild.start_byte : grandchild.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    return _strip_string_quotes(raw)

        # Not a string — no docstring
        break

    return None


def _strip_string_quotes(raw: str) -> str:
    """Strip Python string quotes from a raw string literal."""
    for quote in ('"""', "'''", '"', "'"):
        if raw.startswith(quote) and raw.endswith(quote) and len(raw) >= 2 * len(quote):
            return raw[len(quote) : -len(quote)].strip()
    return raw.strip()


def _get_decorators_python(node, source: bytes) -> list[str]:
    """Extract Python decorators from a function/class definition node."""
    decorators = []
    # Decorators are siblings BEFORE the function/class node in the parent
    parent = node.parent
    if parent is None:
        return decorators

    found_node = False
    for child in reversed(parent.children):
        if child.id == node.id:
            found_node = True
            continue
        if found_node:
            if child.type == "decorator":
                dec_text = (
                    source[child.start_byte : child.end_byte]
                    .decode("utf-8", errors="replace")
                    .strip()
                )
                decorators.insert(0, dec_text)
            else:
                break

    return decorators


def _is_inside_class(node) -> bool:
    """Return True if the node is a direct child of a class body."""
    parent = node.parent
    if parent is None:
        return False
    # Python: parent is 'block', grandparent is 'class_definition'
    if parent.type == "block":
        grandparent = parent.parent
        if grandparent and grandparent.type == "class_definition":
            return True
    # JS/TS: parent is 'class_body'
    if parent.type == "class_body":
        return True
    # Java/C#: parent is 'class_body'
    return parent.type in ("class_body", "declaration_list")


def _get_enclosing_class_name(node, source: bytes) -> str | None:
    """Return enclosing class name for class methods, if present."""
    current = node.parent
    while current is not None:
        if current.type in ("class_definition", "class_declaration"):
            return _get_node_name(current, source)
        current = current.parent
    return None


def _extract_symbols_from_tree(
    root_node,
    source: bytes,
    file_path: str,
    spec: LanguageSpec,
) -> list[Symbol]:
    """Walk the AST and extract all symbols matching the language spec."""
    symbols: list[Symbol] = []

    def walk(node, depth: int = 0) -> None:
        node_type = node.type

        # Determine if this node is a symbol we care about
        is_function = node_type in spec.function_kinds
        is_class = node_type in spec.class_kinds
        is_method = node_type in spec.method_kinds
        is_interface = node_type in spec.interface_kinds

        if is_function or is_class or is_method or is_interface:
            name = _get_node_name(node, source)
            if name:
                # Determine kind
                if is_class:
                    kind = "class"
                elif is_interface and not is_class:
                    kind = "interface"
                elif is_method and _is_inside_class(node):
                    kind = "method"
                elif is_function:
                    kind = "function"
                else:
                    kind = "symbol"

                parent_name = _get_enclosing_class_name(node, source) if kind == "method" else None
                sym_id = make_symbol_id(file_path, kind, name, parent=parent_name)

                # Extract docstring (Python only for now)
                docstring = None
                if spec.name == "python" and node_type in (
                    "function_definition",
                    "class_definition",
                ):
                    docstring = _get_docstring_python(node, source)

                # Extract decorators (Python only for now)
                decorators: list[str] = []
                if spec.name == "python" and node_type == "function_definition":
                    decorators = _get_decorators_python(node, source)

                symbols.append(
                    Symbol(
                        id=sym_id,
                        name=name,
                        kind=kind,
                        file_path=file_path,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        docstring=docstring,
                        decorators=decorators if decorators else None,
                        parent=parent_name,
                    )
                )

        # Recurse into children
        for child in node.children:
            walk(child, depth + 1)

    walk(root_node)
    return symbols


class SymbolExtractor:
    """Extracts symbols from source files using tree-sitter AST parsing.

    Supports all languages in the LanguageSpec registry (Python, JS, TS,
    Go, Rust, Java, C, C#, and more).

    Usage:
        extractor = SymbolExtractor()
        symbols = extractor.extract(Path("src/auth.py"))
    """

    def extract(self, file_path: Path, repo_root: Path | None = None) -> list[Symbol]:
        """Extract symbols from a source file.

        Args:
            file_path: Path to the source file
            repo_root: Optional repo root for computing relative paths in IDs.
                       If None, uses the absolute file path.

        Returns:
            List of Symbol objects. Empty list if the language is unsupported
            or the file cannot be parsed.
        """
        file_path = Path(file_path)
        extension = file_path.suffix.lower()

        spec = get_language_spec(extension)
        if spec is None:
            log.debug("unsupported_language", file=str(file_path), extension=extension)
            return []

        # Compute the path string to use in symbol IDs
        if repo_root is not None:
            try:
                id_path = str(file_path.relative_to(repo_root))
            except ValueError:
                id_path = str(file_path)
        else:
            id_path = str(file_path)

        try:
            source = file_path.read_bytes()
        except OSError as e:
            log.warning("extractor_read_failed", file=str(file_path), error=str(e))
            return []

        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(spec.name)
            tree = parser.parse(source)
        except Exception as e:
            log.warning("extractor_parse_failed", file=str(file_path), lang=spec.name, error=str(e))
            return []

        return _extract_symbols_from_tree(tree.root_node, source, id_path, spec)
