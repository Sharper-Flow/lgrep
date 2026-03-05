"""Tests for the symbol parser package (tk-ajach0NY).

RED phase: tests fail before parser/ package exists.
GREEN phase: all pass after implementation.

Covers:
- Symbol dataclass and stable ID generation (file:kind:name)
- LanguageSpec registry (Python, JS, TS, Go, Rust, Java, C, C#)
- tree-sitter AST walker (function/class/method extraction)
- Byte-offset correctness
- Docstring/decorator extraction
- Stable ID regression across re-index
- File outline and repo outline builders
"""

import textwrap
from pathlib import Path

import pytest


# ============================================================================
# Symbol dataclass and ID generation
# ============================================================================


class TestSymbolDataclass:
    """Symbol dataclass must have the right fields and stable ID format."""

    def test_symbol_importable(self):
        """Symbol must be importable from lgrep.parser.symbols."""
        from lgrep.parser.symbols import Symbol  # noqa: F401

    def test_symbol_has_required_fields(self):
        """Symbol must have: id, name, kind, file_path, start_byte, end_byte."""
        from lgrep.parser.symbols import Symbol

        sym = Symbol(
            id="src/auth.py:function:authenticate",
            name="authenticate",
            kind="function",
            file_path="src/auth.py",
            start_byte=0,
            end_byte=100,
        )
        assert sym.id == "src/auth.py:function:authenticate"
        assert sym.name == "authenticate"
        assert sym.kind == "function"
        assert sym.file_path == "src/auth.py"
        assert sym.start_byte == 0
        assert sym.end_byte == 100

    def test_symbol_id_format(self):
        """Symbol ID must be 'file_path:kind:name'."""
        from lgrep.parser.symbols import Symbol, make_symbol_id

        sym_id = make_symbol_id(file_path="src/auth.py", kind="function", name="authenticate")
        assert sym_id == "src/auth.py:function:authenticate"

    def test_symbol_id_stable_across_calls(self):
        """Same inputs must always produce the same ID."""
        from lgrep.parser.symbols import make_symbol_id

        id1 = make_symbol_id("src/auth.py", "function", "authenticate")
        id2 = make_symbol_id("src/auth.py", "function", "authenticate")
        assert id1 == id2

    def test_symbol_id_changes_on_rename(self):
        """Renaming a symbol must produce a different ID."""
        from lgrep.parser.symbols import make_symbol_id

        id_before = make_symbol_id("src/auth.py", "function", "authenticate")
        id_after = make_symbol_id("src/auth.py", "function", "verify")
        assert id_before != id_after

    def test_symbol_optional_fields(self):
        """Symbol may have optional docstring and decorators fields."""
        from lgrep.parser.symbols import Symbol

        sym = Symbol(
            id="src/auth.py:function:authenticate",
            name="authenticate",
            kind="function",
            file_path="src/auth.py",
            start_byte=0,
            end_byte=100,
            docstring="Authenticate a user.",
            decorators=["@login_required"],
        )
        assert sym.docstring == "Authenticate a user."
        assert sym.decorators == ["@login_required"]


# ============================================================================
# LanguageSpec registry
# ============================================================================


class TestLanguageRegistry:
    """LanguageSpec registry must cover the required languages."""

    def test_language_spec_importable(self):
        """LanguageSpec must be importable."""
        from lgrep.parser.languages import LanguageSpec  # noqa: F401

    def test_get_language_spec_importable(self):
        """get_language_spec must be importable."""
        from lgrep.parser.languages import get_language_spec  # noqa: F401

    def test_python_supported(self):
        """Python must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".py")
        assert spec is not None
        assert spec.name == "python"

    def test_javascript_supported(self):
        """JavaScript must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".js")
        assert spec is not None
        assert spec.name == "javascript"

    def test_typescript_supported(self):
        """TypeScript must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".ts")
        assert spec is not None
        assert spec.name == "typescript"

    def test_go_supported(self):
        """Go must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".go")
        assert spec is not None
        assert spec.name == "go"

    def test_rust_supported(self):
        """Rust must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".rs")
        assert spec is not None
        assert spec.name == "rust"

    def test_java_supported(self):
        """Java must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".java")
        assert spec is not None
        assert spec.name == "java"

    def test_c_supported(self):
        """C must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".c")
        assert spec is not None
        assert spec.name == "c"

    def test_csharp_supported(self):
        """C# must be in the language registry."""
        from lgrep.parser.languages import get_language_spec

        spec = get_language_spec(".cs")
        assert spec is not None
        assert spec.name == "c_sharp"

    def test_unsupported_extension_returns_none(self):
        """Unknown extensions must return None."""
        from lgrep.parser.languages import get_language_spec

        assert get_language_spec(".xyz") is None
        assert get_language_spec(".unknown") is None


# ============================================================================
# Symbol extractor — Python
# ============================================================================


PYTHON_FIXTURE = textwrap.dedent("""\
    \"\"\"Module docstring.\"\"\"

    import os


    def authenticate(user, password):
        \"\"\"Authenticate a user.\"\"\"
        return True


    class UserService:
        \"\"\"Service for user operations.\"\"\"

        def get_user(self, user_id: int):
            \"\"\"Get a user by ID.\"\"\"
            pass

        @staticmethod
        def create_user(name: str):
            pass
""")


class TestPythonExtractor:
    """Symbol extraction from Python source code."""

    def test_extractor_importable(self):
        """SymbolExtractor must be importable."""
        from lgrep.parser.extractor import SymbolExtractor  # noqa: F401

    def test_extract_python_functions(self, tmp_path):
        """Must extract top-level functions from Python."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "authenticate" in names

    def test_extract_python_classes(self, tmp_path):
        """Must extract class definitions from Python."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "UserService" in names

    def test_extract_python_methods(self, tmp_path):
        """Must extract class methods from Python."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "get_user" in names
        assert "create_user" in names

    def test_symbol_kinds_are_correct(self, tmp_path):
        """Symbol kinds must be 'function', 'class', or 'method'."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        by_name = {s.name: s for s in symbols}
        assert by_name["authenticate"].kind == "function"
        assert by_name["UserService"].kind == "class"
        assert by_name["get_user"].kind == "method"

    def test_byte_offsets_are_correct(self, tmp_path):
        """start_byte and end_byte must point to the actual symbol in the file."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)
        content = src_file.read_bytes()

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        by_name = {s.name: s for s in symbols}
        auth_sym = by_name["authenticate"]

        # The byte range must contain the function definition
        snippet = content[auth_sym.start_byte : auth_sym.end_byte].decode("utf-8")
        assert "authenticate" in snippet
        assert auth_sym.start_byte < auth_sym.end_byte

    def test_docstring_extraction(self, tmp_path):
        """Must extract docstrings from functions and classes."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        by_name = {s.name: s for s in symbols}
        assert by_name["authenticate"].docstring is not None
        assert "Authenticate" in by_name["authenticate"].docstring
        assert by_name["UserService"].docstring is not None

    def test_decorator_extraction(self, tmp_path):
        """Must extract decorators from decorated functions/methods."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        by_name = {s.name: s for s in symbols}
        create_user = by_name["create_user"]
        assert create_user.decorators is not None
        assert len(create_user.decorators) > 0
        assert any("staticmethod" in d for d in create_user.decorators)

    def test_stable_id_regression(self, tmp_path):
        """Symbol IDs must be identical across two extraction runs."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        extractor = SymbolExtractor()
        run1 = {s.name: s.id for s in extractor.extract(src_file)}
        run2 = {s.name: s.id for s in extractor.extract(src_file)}

        assert run1 == run2, "Symbol IDs must be stable across re-extraction"


# ============================================================================
# Symbol extractor — JavaScript
# ============================================================================


JS_FIXTURE = textwrap.dedent("""\
    /**
     * Authenticate a user.
     */
    function authenticate(user, password) {
        return true;
    }

    class UserService {
        getUser(userId) {
            return null;
        }

        static createUser(name) {
            return {};
        }
    }
""")


class TestJavaScriptExtractor:
    """Symbol extraction from JavaScript source code."""

    def test_extract_js_functions(self, tmp_path):
        """Must extract top-level functions from JavaScript."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.js"
        src_file.write_text(JS_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "authenticate" in names

    def test_extract_js_classes(self, tmp_path):
        """Must extract class definitions from JavaScript."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.js"
        src_file.write_text(JS_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "UserService" in names

    def test_extract_js_methods(self, tmp_path):
        """Must extract class methods from JavaScript."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.js"
        src_file.write_text(JS_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "getUser" in names


# ============================================================================
# Symbol extractor — TypeScript
# ============================================================================


TS_FIXTURE = textwrap.dedent("""\
    interface User {
        id: number;
        name: string;
    }

    function authenticate(user: User, password: string): boolean {
        return true;
    }

    class UserService {
        getUser(userId: number): User | null {
            return null;
        }
    }
""")


class TestTypeScriptExtractor:
    """Symbol extraction from TypeScript source code."""

    def test_extract_ts_functions(self, tmp_path):
        """Must extract functions from TypeScript."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.ts"
        src_file.write_text(TS_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "authenticate" in names

    def test_extract_ts_interfaces(self, tmp_path):
        """Must extract interfaces from TypeScript."""
        from lgrep.parser.extractor import SymbolExtractor

        src_file = tmp_path / "auth.ts"
        src_file.write_text(TS_FIXTURE)

        extractor = SymbolExtractor()
        symbols = extractor.extract(src_file)

        names = [s.name for s in symbols]
        assert "User" in names


# ============================================================================
# File outline builder
# ============================================================================


class TestFileOutline:
    """File outline must return a structured summary of symbols in a file."""

    def test_file_outline_importable(self):
        """build_file_outline must be importable."""
        from lgrep.parser.hierarchy import build_file_outline  # noqa: F401

    def test_file_outline_structure(self, tmp_path):
        """build_file_outline must return a dict with file_path and symbols list."""
        from lgrep.parser.hierarchy import build_file_outline

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        outline = build_file_outline(src_file)

        assert "file_path" in outline
        assert "symbols" in outline
        assert isinstance(outline["symbols"], list)
        assert len(outline["symbols"]) > 0

    def test_file_outline_symbol_fields(self, tmp_path):
        """Each symbol in the outline must have name, kind, and id."""
        from lgrep.parser.hierarchy import build_file_outline

        src_file = tmp_path / "auth.py"
        src_file.write_text(PYTHON_FIXTURE)

        outline = build_file_outline(src_file)

        for sym in outline["symbols"]:
            assert "name" in sym
            assert "kind" in sym
            assert "id" in sym


# ============================================================================
# Repo outline builder
# ============================================================================


class TestRepoOutline:
    """Repo outline must aggregate file outlines across a directory."""

    def test_repo_outline_importable(self):
        """build_repo_outline must be importable."""
        from lgrep.parser.hierarchy import build_repo_outline  # noqa: F401

    def test_repo_outline_structure(self, tmp_path):
        """build_repo_outline must return a dict with repo_path and files list."""
        from lgrep.parser.hierarchy import build_repo_outline

        # Create a small project
        (tmp_path / "auth.py").write_text(PYTHON_FIXTURE)
        (tmp_path / "utils.py").write_text("def helper(): pass\n")

        outline = build_repo_outline(tmp_path)

        assert "repo_path" in outline
        assert "files" in outline
        assert isinstance(outline["files"], list)
        assert len(outline["files"]) >= 2

    def test_repo_outline_contains_file_outlines(self, tmp_path):
        """Each entry in files must be a valid file outline."""
        from lgrep.parser.hierarchy import build_repo_outline

        (tmp_path / "auth.py").write_text(PYTHON_FIXTURE)

        outline = build_repo_outline(tmp_path)

        for file_outline in outline["files"]:
            assert "file_path" in file_outline
            assert "symbols" in file_outline
