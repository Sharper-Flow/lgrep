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
    # Go: the declared alias name is the first type_identifier under
    # type_alias (the "name" field may be unavailable in pinned bindings).
    if parent.type == "type_alias" and node.type == "type_identifier":
        name_node = parent.child_by_field_name("name")
        if name_node is not None:
            return name_node.id == node.id
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


def _go_occurrence_kind(node, parent, occ_name: str = "", import_qualifiers=frozenset()) -> str:
    """Classify a Go candidate occurrence (identifier / field_identifier /
    type_identifier) by its AST context. Mirrors the Python kinds; a
    selector operand matching the file's import table classifies import."""
    if parent is None:
        return "reference"

    # Qualified type (Go type positions: alias RHS, struct field types,
    # parameter types). The package_identifier child is the package
    # qualifier -> import usage; the type_identifier member -> attribute
    # (selector-member mirror). Probe-verified: type positions use
    # qualified_type, not selector_expression.
    if parent.type == "qualified_type":
        if node.type == "package_identifier":
            return "import" if occ_name in import_qualifiers else "reference"
        if node.type == "type_identifier":
            return "attribute"

    # Selector member: the field of `operand.field` is the last child of a
    # selector_expression. Classified as attribute regardless of whether the
    # selector is being called (Python-mirror behavior).
    if (
        parent.type == "selector_expression"
        and parent.children
        and parent.children[-1].id == node.id
    ):
        return "attribute"

    # Selector operand matching the import table: the package qualifier of
    # `qualifier.Member` (plain or aliased import) — an import usage, not an
    # ordinary reference.
    if parent.type == "selector_expression" and occ_name in import_qualifiers:
        operand = parent.child_by_field_name("operand")
        operand_id = operand.id if operand is not None else parent.children[0].id
        if operand_id == node.id:
            return "import"

    # Plain call target: identifier is the called expression of a call.
    if parent.type == "call_expression" and parent.children and parent.children[0].id == node.id:
        return "call"

    return "reference"


def _go_parse_import_spec(spec_node, source: bytes):
    """Return (qualifier, alias_node) for one Go import_spec.

    qualifier = explicit package_identifier alias when present, else the
    last path segment. blank_identifier and dot aliases yield (None, None)
    — no qualifier exists. alias_node is the package_identifier for
    explicitly aliased imports (import-site occurrence anchor), else None.
    """
    alias_node = None
    path_node = None
    for child in spec_node.children:
        if child.type == "package_identifier":
            alias_node = child
        elif child.type in ("interpreted_string_literal", "raw_string_literal"):
            path_node = child
        elif child.type in ("blank_identifier", "dot"):
            return None, None
    if path_node is None:
        return None, None
    if alias_node is not None:
        qualifier = source[alias_node.start_byte : alias_node.end_byte].decode(
            "utf-8", errors="replace"
        )
        return qualifier, alias_node
    raw = source[path_node.start_byte : path_node.end_byte].decode("utf-8", errors="replace")
    path = raw.strip('"`')
    if not path:
        return None, None
    return path.rsplit("/", 1)[-1], None


def _is_go_excluded_identifier(node, parent) -> bool:
    """Return True for Go identifier-position nodes that name declarations
    rather than usages, beyond what _is_definition_name already covers:
    struct field names and interface method-element names. The type part of a
    field_declaration (a type_identifier sibling) is a usage and stays."""
    if parent is None:
        return False
    return node.type == "field_identifier" and parent.type in ("field_declaration", "method_elem")


def _go_spec_kind(spec_node) -> str:
    """Classify one Go type_spec by scanning its children by node type
    (ordinal positions break on generics, which insert
    type_parameter_list). struct_type -> class, interface_type -> interface;
    other forms keep the pre-existing class fallback."""
    for child in spec_node.children:
        if child.type == "struct_type":
            return "class"
        if child.type == "interface_type":
            return "interface"
    return "class"


def _go_type_declaration_symbols(node, source: bytes, file_path: str) -> list[Symbol]:
    """Emit one Symbol per type_spec / type_alias child of a Go
    type_declaration.

    Grouped declarations carry multiple (mixable) type_spec and type_alias
    children; extracting only the first spec silently dropped specs 2..n,
    and aliases (a distinct type_alias node, not type_spec) were dropped
    entirely. Byte ranges come from the child node so each spec/alias gets
    its own precise range. Nothing tracked nests inside these children
    (methods are sibling method_declaration nodes), so no enclosing id.
    """
    emitted: list[Symbol] = []
    for child in node.children:
        if child.type == "type_spec":
            kind = _go_spec_kind(child)
        elif child.type == "type_alias":
            kind = "alias"
        else:
            continue
        # The "name" field is unavailable for type_spec in some pinned
        # bindings; the first type_identifier descendant is the declared
        # name for both node types (probe-verified).
        name_node = child.child_by_field_name("name")
        if name_node is None:
            name_node = _find_type_identifier(child)
        if name_node is None:
            continue
        name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        emitted.append(
            Symbol(
                id=make_symbol_id(file_path, kind, name, parent=None),
                name=name,
                kind=kind,
                file_path=file_path,
                start_byte=child.start_byte,
                end_byte=child.end_byte,
                docstring=None,
                decorators=None,
                parent=None,
            )
        )
    return emitted


def _find_type_identifier(node):
    """Return the first type_identifier descendant, else None.

    Go receiver types may be wrapped in pointer_type and generic_type nodes,
    so a bounded child-depth scan would miss ``*Receiver[T]``.
    """
    if node.type == "type_identifier":
        return node
    for child in node.children:
        found = _find_type_identifier(child)
        if found is not None:
            return found
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

    # Go import table pre-pass (root children only — Go imports are always
    # file-top-level). Builds the qualifier set used by _go_occurrence_kind
    # and emits import-site occurrences for explicit aliases in this
    # controlled context (package_identifier must stay out of the main gate
    # — it would catch the `package main` clause identifier).
    go_import_qualifiers: set[str] = set()
    if spec.name == "go":
        for top in root_node.children:
            if top.type != "import_declaration":
                continue
            spec_nodes = []
            for child in top.children:
                if child.type == "import_spec":
                    spec_nodes.append(child)
                elif child.type == "import_spec_list":
                    spec_nodes.extend(c for c in child.children if c.type == "import_spec")
            for spec_node in spec_nodes:
                qualifier, alias_node = _go_parse_import_spec(spec_node, source)
                if not qualifier:
                    continue
                go_import_qualifiers.add(qualifier)
                if alias_node is not None:
                    line_number = alias_node.start_point[0] + 1
                    if lines_decoded is None:
                        lines_decoded = source.decode("utf-8", errors="replace").splitlines()
                    line_text = (
                        lines_decoded[line_number - 1] if line_number <= len(lines_decoded) else ""
                    )
                    occurrences.append(
                        Occurrence(
                            id=make_occurrence_id(file_path, qualifier, alias_node.start_byte),
                            name=qualifier,
                            file_path=file_path,
                            start_byte=alias_node.start_byte,
                            end_byte=alias_node.end_byte,
                            line_number=line_number,
                            line_text=line_text,
                            kind="import",
                            enclosing_symbol_id=None,
                        )
                    )

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

        # Go type_declaration: one symbol per type_spec / type_alias child
        # (grouped declarations, mixed groups, and standalone aliases).
        # Handled outside the generic single-symbol path; nothing tracked
        # nests inside these children, so no enclosing id is pushed.
        go_type_decl = spec.name == "go" and node_type == "type_declaration"

        if is_function or is_class or is_method or is_interface:
            if go_type_decl:
                symbols.extend(_go_type_declaration_symbols(node, source, file_path))
                name = None
            else:
                name = _get_node_name(node, source)
            if name:
                if spec.name == "go" and node_type == "method_declaration":
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
        parent = node.parent
        is_python_occ = spec.name == "python" and node_type == "identifier"
        is_go_occ = spec.name == "go" and (
            node_type in ("identifier", "field_identifier", "type_identifier")
            or (
                # package_identifier only under qualified_type — the import
                # table classifies it; import_spec aliases are emitted in the
                # pre-pass and the package_clause name must never be one.
                node_type == "package_identifier"
                and parent is not None
                and parent.type == "qualified_type"
            )
        )
        if (
            (is_python_occ or is_go_occ)
            and not _is_definition_name(node, parent, spec)
            and not (is_go_occ and _is_go_excluded_identifier(node, parent))
        ):
            occ_name = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            # The blank identifier `_` (a plain `identifier` outside
            # import specs) is never a candidate occurrence.
            if is_go_occ and occ_name == "_":
                pass
            else:
                kind = (
                    _occurrence_kind(node, parent)
                    if is_python_occ
                    else _go_occurrence_kind(node, parent, occ_name, go_import_qualifiers)
                )
                if kind in _OCCURRENCE_KINDS:
                    line_number = node.start_point[0] + 1
                    if lines_decoded is None:
                        lines_decoded = source.decode("utf-8", errors="replace").splitlines()
                    line_text = (
                        lines_decoded[line_number - 1] if line_number <= len(lines_decoded) else ""
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
