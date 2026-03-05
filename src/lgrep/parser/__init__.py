"""lgrep symbol parser package.

Provides tree-sitter-based symbol extraction for 165+ languages.

Components:
- symbols: Symbol dataclass and stable ID generation (file:kind:name)
- languages: LanguageSpec registry mapping file extensions to parsers
- extractor: SymbolExtractor — tree-sitter AST walker
- hierarchy: File and repo outline builders
"""
