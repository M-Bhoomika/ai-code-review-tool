"""Tree-sitter AST-aware code chunking.

Extracts semantically meaningful units (functions, methods, classes) from source
files for supported languages, instead of splitting on fixed line windows. Each
extracted chunk carries its symbol name, enclosing class (if any), file path,
line span, and language.

Design notes:

* Tree-sitter and the grammar pack are imported lazily so this module (and the
  indexer that depends on it) can be imported even when those packages are not
  installed. In that case AST chunking simply reports "unavailable" and callers
  fall back to line-based chunking.
* ``chunk_code`` returns ``None`` to signal the caller should fall back: this
  happens for unsupported languages, when grammars are unavailable, on parse
  failure, or when a file contains no extractable symbols (e.g. a config-like or
  script file with only top-level statements). This keeps existing indexing
  behavior intact for those cases.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("ai-code-review-worker.ast_chunker")

# Languages we can parse into AST chunks. Anything else falls back.
SUPPORTED_LANGUAGES: set[str] = {
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "cpp",
}

# Map our internal language names to tree-sitter grammar names.
_TS_GRAMMAR_NAME: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "java": "java",
    "go": "go",
    "cpp": "cpp",
}

# Node types that represent functions/methods, per language.
_FUNCTION_TYPES: dict[str, set[str]] = {
    "python": {"function_definition"},
    "javascript": {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
    },
    "typescript": {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
    },
    "java": {"method_declaration", "constructor_declaration"},
    "go": {"function_declaration", "method_declaration"},
    "cpp": {"function_definition"},
}

# Node types that represent classes (or class-like containers), per language.
_CLASS_TYPES: dict[str, set[str]] = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
    },
    "java": {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
    },
    "go": set(),  # Go has no classes
    "cpp": {"class_specifier", "struct_specifier"},
}

# Node types whose text can serve as a symbol name when no "name" field exists.
_NAME_NODE_TYPES = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "qualified_identifier",
    "property_identifier",
}

KIND_CLASS = "class"
KIND_FUNCTION = "function"


@dataclass
class ExtractedSymbol:
    kind: str
    name: Optional[str]
    parent_class: Optional[str]
    start_line: int
    end_line: int
    source: str


# Cache one parser per language; sentinel marks "tried and unavailable".
_PARSER_CACHE: dict[str, Any] = {}
_UNAVAILABLE = object()


def _node_kind(node: Any) -> str:
    """Return the tree-sitter node type/kind across binding versions."""
    if hasattr(node, "kind"):
        kind = node.kind
        return kind() if callable(kind) else kind
    return node.type


def _node_byte(node: Any, edge: str) -> int:
    """Return start/end byte offset for a node across binding versions."""
    accessor = getattr(node, f"{edge}_byte")
    return accessor() if callable(accessor) else accessor


def _node_line(node: Any, edge: str) -> int:
    """Return 1-based start/end line for a node across binding versions."""
    point_attr = f"{edge}_point"
    if hasattr(node, point_attr):
        point = getattr(node, point_attr)
        return point[0] + 1
    position_attr = f"{edge}_position"
    position = getattr(node, position_attr)
    if callable(position):
        position = position()
    return position.row + 1


def _iter_children(node: Any):
    """Yield child nodes regardless of tree-sitter Python binding version."""
    if hasattr(node, "children"):
        yield from node.children
        return
    child_count = node.child_count() if callable(node.child_count) else node.child_count
    for index in range(child_count):
        yield node.child(index)


def _parse_tree(parser: Any, content: str) -> Any:
    """Parse source text, supporting str- and bytes-based tree-sitter bindings."""
    try:
        return parser.parse(content)
    except TypeError:
        return parser.parse(content.encode("utf-8", "ignore"))


def _tree_root(tree: Any) -> Any:
    """Return the root node from a parse tree across binding versions."""
    root = tree.root_node
    return root() if callable(root) else root


def _get_parser(language: str) -> Optional[Any]:
    """Return a tree-sitter parser for ``language``, or None if unavailable."""
    if language in _PARSER_CACHE:
        cached = _PARSER_CACHE[language]
        return None if cached is _UNAVAILABLE else cached

    grammar = _TS_GRAMMAR_NAME.get(language)
    parser: Optional[Any] = None
    if grammar is not None:
        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(grammar)
        except Exception:  # noqa: BLE001 - any failure => fall back
            try:
                from tree_sitter_languages import get_parser as _legacy_get_parser

                parser = _legacy_get_parser(grammar)
            except Exception:  # noqa: BLE001
                parser = None

    if parser is None:
        logger.info("ast_parser_unavailable", extra={"language": language})
    _PARSER_CACHE[language] = parser if parser is not None else _UNAVAILABLE
    return parser


def is_supported(language: str) -> bool:
    """Return True if AST chunking can (in principle) handle this language."""
    return language in SUPPORTED_LANGUAGES


def _text(node: Any, source: str) -> str:
    start = _node_byte(node, "start")
    end = _node_byte(node, "end")
    return source[start:end]


def _find_first_name(node: Any, source: str) -> Optional[str]:
    """Best-effort name lookup for nodes without a ``name`` field (e.g. C++)."""
    # Prefer drilling through declarator chains (C/C++ function definitions).
    declarator = node.child_by_field_name("declarator")
    seen = 0
    while declarator is not None and seen < 8:
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            break
        declarator = inner
        seen += 1

    search_root = declarator if declarator is not None else node
    stack = [search_root]
    visited = 0
    while stack and visited < 200:
        current = stack.pop()
        visited += 1
        if _node_kind(current) in _NAME_NODE_TYPES:
            return _text(current, source)
        # Shallow search: don't descend into nested bodies.
        if _node_kind(current) in {"block", "compound_statement", "class_body"}:
            continue
        stack.extend(reversed(list(_iter_children(current))))
    return None


def _node_name(node: Any, source: str) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _text(name_node, source)
    return _find_first_name(node, source)


def _extract_symbols(
    root: Any, language: str, source: str
) -> list[ExtractedSymbol]:
    function_types = _FUNCTION_TYPES.get(language, set())
    class_types = _CLASS_TYPES.get(language, set())
    symbols: list[ExtractedSymbol] = []

    def visit(node: Any, parent_class: Optional[str]) -> None:
        node_type = _node_kind(node)
        next_parent = parent_class

        if node_type in class_types:
            name = _node_name(node, source)
            symbols.append(
                ExtractedSymbol(
                    kind=KIND_CLASS,
                    name=name,
                    parent_class=parent_class,
                    start_line=_node_line(node, "start"),
                    end_line=_node_line(node, "end"),
                    source=_text(node, source),
                )
            )
            # Methods directly inside this class get it as their parent.
            next_parent = name or parent_class
        elif node_type in function_types:
            name = _node_name(node, source)
            symbols.append(
                ExtractedSymbol(
                    kind=KIND_FUNCTION,
                    name=name,
                    parent_class=parent_class,
                    start_line=_node_line(node, "start"),
                    end_line=_node_line(node, "end"),
                    source=_text(node, source),
                )
            )

        for child in _iter_children(node):
            visit(child, next_parent)

    visit(root, None)
    return symbols


def extract_symbols(content: str, language: str) -> Optional[list[ExtractedSymbol]]:
    """Parse ``content`` and return extracted symbols, or None to fall back.

    Returns None when the language is unsupported, the grammar/tree-sitter is
    unavailable, parsing fails, or no symbols are found.
    """
    if language not in SUPPORTED_LANGUAGES:
        return None

    parser = _get_parser(language)
    if parser is None:
        return None

    try:
        tree = _parse_tree(parser, content)
    except Exception as exc:  # noqa: BLE001 - parse failure => fall back
        logger.warning(
            "ast_parse_failed", extra={"language": language, "error": str(exc)}
        )
        return None

    symbols = _extract_symbols(_tree_root(tree), language, content)
    if not symbols:
        return None
    return symbols


def chunk_code(
    content: str,
    repository: str,
    file_path: str,
    language: str,
) -> Optional[list[Any]]:
    """Return AST-aware :class:`CodeChunk` objects, or None to fall back.

    The return type is the indexer's ``CodeChunk`` (imported lazily to avoid an
    import cycle). ``None`` signals the caller to use line-based chunking.
    """
    symbols = extract_symbols(content, language)
    if symbols is None:
        return None

    # Lazy import avoids a circular import with repository_indexer.
    from app.repository_indexer import CodeChunk

    chunks: list[Any] = []
    for symbol in symbols:
        chunk_id = (
            f"{repository}:{file_path}:"
            f"{symbol.start_line}-{symbol.end_line}:"
            f"{symbol.kind}:{symbol.name or 'anonymous'}"
        )
        chunks.append(
            CodeChunk(
                chunk_id=chunk_id,
                repository=repository,
                file_path=file_path,
                language=language,
                content=symbol.source,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                symbol_name=symbol.name,
                parent_class=symbol.parent_class,
                node_kind=symbol.kind,
            )
        )

    logger.info(
        "ast_chunked_file",
        extra={
            "file_path": file_path,
            "language": language,
            "symbols": len(chunks),
        },
    )
    return chunks
