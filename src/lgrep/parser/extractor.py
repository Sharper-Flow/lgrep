"""Symbol extractor using tree-sitter AST parsing.

Walks the AST of a source file and extracts Symbol objects for each
function, class, method, and interface definition. For Python, it also
collects candidate identifier Occurrence objects for local reference lookup.

Uses tree-sitter-language-pack for pre-built parsers (165+ languages).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lgrep.parser.languages import LanguageSpec, get_language_spec
from lgrep.parser.symbols import Occurrence, Symbol, make_occurrence_id, make_symbol_id

log = structlog.get_logger()

_OCCURRENCE_KINDS = frozenset({"call", "attribute", "import", "reference"})


def _get_node_name(node, source: bytes) -> str | None:
    """Extract the name from a named node (function/class/method definition)."""
    # Try common name child node types. field_identifier is included for Go:
    # tree-sitter-go puts method names in field_identifier nodes, and no other
    # registered language emits field_identifier as a direct child of a
    # definition node (verified against grammar source + executable probe).
    for child in node.children:
        if child.type in (
            "identifier",
            "name",
            "type_identifier",
            "property_identifier",
            "field_identifier",
        ):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
    # Go type_declaration: the declared name is the type_spec's type_identifier
    # child (a grandchild of the type_declaration node).
    if node.type == "type_declaration":
        for child in node.children:
            if child.type == "type_spec":
                for grandchild in child.children:
                    if grandchild.type == "type_identifier":
                        return source[grandchild.start_byte : grandchild.end_byte].decode(
                            "utf-8", errors="replace"
                        )
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


def _is_definition_name(node, parent, spec: LanguageSpec) -> bool:
    """Return True if this identifier node is the name of a tracked definition."""
    if parent is None:
        return False
    if parent.type in (*spec.function_kinds, *spec.class_kinds):
        for child in parent.children:
            if child.type in ("identifier", "name", "type_identifier", "field_identifier"):
                return child.id == node.id
    # Go: the declared type name is the first type_identifier under type_spec.
    # Its parent is type_spec, not the type_declaration, so the branch above
    # cannot catch it.
    if parent.type == "type_spec" and node.type == "type_identifier":
        for child in parent.children:
            if child.type == "type_identifier":
                return child.id == node.id
    return False


def _occurrence_kind(node, parent) -> str:
    """Classify a candidate identifier occurrence by its AST context."""
    if parent is None:
        return "reference"

    # Imports: identifier may be nested under dotted_name / aliased_import.
    current = parent
    while current is not None:
        if current.type in ("import_statement", "import_from_statement", "aliased_import"):
            return "import"
        current = current.parent

    parent_type = parent.type

    # Attribute access: attribute name is the third child (obj, dot, attr)
    if parent_type == "attribute" and len(parent.children) >= 3:
        if parent.children[2].id == node.id:
            return "attribute"
        return "reference"

    # Function call target when identifier is the called expression
    if parent_type == "call" and parent.children and parent.children[0].id == node.id:
        return "call"

    return "reference"


def _go_occurrence_kind(node, parent) -> str:
    """Classify a Go candidate occurrence (identifier / field_identifier /
    type_identifier) by its AST context. Mirrors the Python kinds."""
    if parent is None:
        return "reference"

    # Selector member: the field of `operand.field` is the last child of a
    # selector_expression. Classified as attribute regardless of whether the
    # selector is being called (Python-mirror behavior).
    if (
        parent.type == "selector_expression"
        and parent.children
        and parent.children[-1].id == node.id
    ):
        return "attribute"

    # Plain call target: identifier is the called expression of a call.
    if parent.type == "call_expression" and parent.children and parent.children[0].id == node.id:
        return "call"

    return "reference"


def _is_go_excluded_identifier(node, parent) -> bool:
    """Return True for Go identifier-position nodes that name declarations
    rather than usages, beyond what _is_definition_name already covers:
    struct field names and interface method-element names. The type part of a
    field_declaration (a type_identifier sibling) is a usage and stays."""
    if parent is None:
        return False
    return node.type == "field_identifier" and parent.type in ("field_declaration", "method_elem")


def _go_type_kind(node) -> str:
    """Classify a Go type_declaration by scanning type_spec children by node
    type (ordinal positions break on generics, which insert
    type_parameter_list). struct_type -> class, interface_type -> interface;
    type aliases and other forms keep the pre-existing class fallback."""
    for child in node.children:
        if child.type == "type_spec":
            for grandchild in child.children:
                if grandchild.type == "struct_type":
                    return "class"
                if grandchild.type == "interface_type":
                    return "interface"
    return "class"


def _find_type_identifier(node):
    """Return the first type_identifier descendant within two levels (direct
    child, or wrapped under pointer_type/qualified_type), else None."""
    if node.type == "type_identifier":
        return node
    for child in node.children:
        if child.type == "type_identifier":
            return child
        for grandchild in child.children:
            if grandchild.type == "type_identifier":
                return grandchild
    return None


def _go_receiver_type_name(node, source: bytes) -> str | None:
    """Return the receiver type name of a Go method_declaration.

    The receiver is the FIRST parameter_list child (grammar-required, ahead
    of the method name). Its type is a type_identifier, possibly wrapped in
    pointer_type. Returns None when no receiver type resolves.
    """
    for child in node.children:
        if child.type == "parameter_list":
            for param in child.children:
                if param.type == "parameter_declaration":
                    found = _find_type_identifier(param)
                    if found is not None:
                        return source[found.start_byte : found.end_byte].decode(
                            "utf-8", errors="replace"
                        )
            return None
    return None


def _extract_symbols_and_occurrences_from_tree(
    root_node,
    source: bytes,
    file_path: str,
    spec: LanguageSpec,
) -> tuple[list[Symbol], list[Occurrence]]:
    """Walk the AST and extract symbols plus Python/Go candidate occurrences."""
    symbols: list[Symbol] = []
    occurrences: list[Occurrence] = []
    lines_decoded: list[str] | None = None

    def _enclosing_id(stack: list[tuple]) -> str | None:
        for _, sym_id in reversed(stack):
            if sym_id is not None:
                return sym_id
        return None

    def walk(node, depth: int = 0, enclosing_stack: list[tuple] | None = None) -> None:
        nonlocal lines_decoded
        if enclosing_stack is None:
            enclosing_stack = []

        node_type = node.type

        # Determine if this node is a symbol we care about
        is_function = node_type in spec.function_kinds
        is_class = node_type in spec.class_kinds
        is_method = node_type in spec.method_kinds
        is_interface = node_type in spec.interface_kinds

        sym_id_for_children: str | None = None

        if is_function or is_class or is_method or is_interface:
            name = _get_node_name(node, source)
            if name:
                if spec.name == "go" and node_type == "type_declaration":
                    kind = _go_type_kind(node)
                elif spec.name == "go" and node_type == "method_declaration":
                    # tree-sitter-go guarantees a receiver parameter_list on
                    # method_declaration; _is_inside_class never matches
                    # top-level Go methods.
                    kind = "method"
                elif is_class:
                    kind = "class"
                elif is_interface and not is_class:
                    kind = "interface"
                elif is_method and _is_inside_class(node):
                    kind = "method"
                elif is_function:
                    kind = "function"
                else:
                    kind = "symbol"

                if kind == "method":
                    if spec.name == "go" and node_type == "method_declaration":
                        parent_name = _go_receiver_type_name(node, source)
                    else:
                        parent_name = _get_enclosing_class_name(node, source)
                else:
                    parent_name = None
                sym_id_for_children = make_symbol_id(file_path, kind, name, parent=parent_name)

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
                        id=sym_id_for_children,
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

        # Candidate occurrence extraction (Python and Go)
        is_python_occ = spec.name == "python" and node_type == "identifier"
        is_go_occ = spec.name == "go" and node_type in (
            "identifier",
            "field_identifier",
            "type_identifier",
        )
        if is_python_occ or is_go_occ:
            parent = node.parent
            if not _is_definition_name(node, parent, spec) and not (
                is_go_occ and _is_go_excluded_identifier(node, parent)
            ):
                kind = (
                    _occurrence_kind(node, parent)
                    if is_python_occ
                    else _go_occurrence_kind(node, parent)
                )
                if kind in _OCCURRENCE_KINDS:
                    line_number = node.start_point[0] + 1
                    if lines_decoded is None:
                        lines_decoded = source.decode("utf-8", errors="replace").splitlines()
                    line_text = (
                        lines_decoded[line_number - 1] if line_number <= len(lines_decoded) else ""
                    )
                    occ_name = source[node.start_byte : node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    occurrences.append(
                        Occurrence(
                            id=make_occurrence_id(file_path, occ_name, node.start_byte),
                            name=occ_name,
                            file_path=file_path,
                            start_byte=node.start_byte,
                            end_byte=node.end_byte,
                            line_number=line_number,
                            line_text=line_text,
                            kind=kind,
                            enclosing_symbol_id=_enclosing_id(enclosing_stack),
                        )
                    )

        # Push the current symbol onto the enclosing stack before recursing
        child_stack = enclosing_stack
        if sym_id_for_children is not None:
            child_stack = [*enclosing_stack, (node, sym_id_for_children)]

        # Recurse into children
        for child in node.children:
            walk(child, depth + 1, child_stack)

    walk(root_node)
    return symbols, occurrences


def _extract_symbols_from_tree(
    root_node,
    source: bytes,
    file_path: str,
    spec: LanguageSpec,
) -> list[Symbol]:
    """Walk the AST and extract all symbols matching the language spec."""
    symbols, _ = _extract_symbols_and_occurrences_from_tree(root_node, source, file_path, spec)
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
        result = self._parse(file_path, repo_root)
        if result is None:
            return []
        root_node, source, id_path, spec = result
        return _extract_symbols_from_tree(root_node, source, id_path, spec)

    def extract_full(
        self, file_path: Path, repo_root: Path | None = None
    ) -> tuple[list[Symbol], list[Occurrence]]:
        """Extract symbols and candidate occurrences from a source file.

        Args:
            file_path: Path to the source file
            repo_root: Optional repo root for computing relative paths in IDs.
                       If None, uses the absolute file path.

        Returns:
            Tuple of (symbols, occurrences). Occurrences are currently
            collected for Python and Go; other languages return an empty list.
            Empty lists are returned if the language is unsupported or the
            file cannot be parsed.
        """
        result = self._parse(file_path, repo_root)
        if result is None:
            return [], []
        root_node, source, id_path, spec = result
        return _extract_symbols_and_occurrences_from_tree(root_node, source, id_path, spec)

    def _parse(self, file_path: Path, repo_root: Path | None = None) -> tuple | None:
        """Parse a source file and return tree-sitter metadata."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower()

        spec = get_language_spec(extension)
        if spec is None:
            log.debug("unsupported_language", file=str(file_path), extension=extension)
            return None

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
            return None

        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(spec.name)
            tree = parser.parse(source)
        except Exception as e:
            log.warning("extractor_parse_failed", file=str(file_path), lang=spec.name, error=str(e))
            return None

        return tree.root_node, source, id_path, spec
