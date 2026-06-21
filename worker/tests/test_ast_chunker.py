"""Tests for tree-sitter AST-aware chunking.

These tests require the tree-sitter grammar pack. If it is not installed the
whole module is skipped so the rest of the worker suite still runs.
"""
import pytest

pytest.importorskip("tree_sitter_language_pack")

from app import ast_chunker
from app.repository_indexer import CodeChunk

PY_SOURCE = '''\
import os


def top_level(a, b):
    return a + b


class Service:
    def method_one(self):
        return 1

    def method_two(self, value):
        def inner_helper():
            return value
        return inner_helper()
'''


def _by_name(chunks):
    return {c.symbol_name: c for c in chunks}


def _skip_if_no_parser(language: str) -> None:
    if ast_chunker._get_parser(language) is None:
        pytest.skip(f"tree-sitter grammar for {language} unavailable")


def test_is_supported():
    assert ast_chunker.is_supported("python")
    assert ast_chunker.is_supported("cpp")
    assert not ast_chunker.is_supported("ruby")
    assert not ast_chunker.is_supported("unknown")


def test_python_functions_extracted():
    _skip_if_no_parser("python")
    chunks = ast_chunker.chunk_code(PY_SOURCE, "repo", "svc.py", "python")

    assert chunks is not None
    assert all(isinstance(c, CodeChunk) for c in chunks)
    by_name = _by_name(chunks)

    top = by_name["top_level"]
    assert top.node_kind == "function"
    assert top.parent_class is None
    assert "def top_level" in top.content


def test_classes_extracted():
    _skip_if_no_parser("python")
    chunks = ast_chunker.chunk_code(PY_SOURCE, "repo", "svc.py", "python")
    by_name = _by_name(chunks)

    service = by_name["Service"]
    assert service.node_kind == "class"
    assert service.parent_class is None
    assert "class Service" in service.content


def test_nested_methods_extracted():
    _skip_if_no_parser("python")
    chunks = ast_chunker.chunk_code(PY_SOURCE, "repo", "svc.py", "python")
    by_name = _by_name(chunks)

    assert "method_one" in by_name
    assert "method_two" in by_name
    assert by_name["method_one"].node_kind == "function"
    assert by_name["method_one"].parent_class == "Service"
    assert by_name["method_two"].parent_class == "Service"
    # A function nested inside a method is still captured.
    assert "inner_helper" in by_name


def test_metadata_correctness():
    _skip_if_no_parser("python")
    chunks = ast_chunker.chunk_code(PY_SOURCE, "octocat/hello", "svc.py", "python")
    by_name = _by_name(chunks)

    method = by_name["method_one"]
    assert method.repository == "octocat/hello"
    assert method.file_path == "svc.py"
    assert method.language == "python"
    assert method.start_line >= 1
    assert method.end_line >= method.start_line
    # Chunk ids are unique per symbol.
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_unsupported_language_falls_back_to_none():
    # Unsupported languages signal the caller to use line-based chunking.
    assert ast_chunker.chunk_code("def f(): pass", "repo", "a.rb", "ruby") is None
    assert ast_chunker.chunk_code("x = 1", "repo", "a.txt", "unknown") is None


def test_no_symbols_falls_back_to_none():
    _skip_if_no_parser("python")
    # A file with only top-level statements yields no functions/classes.
    assert ast_chunker.chunk_code("x = 1\ny = 2\n", "repo", "c.py", "python") is None


def test_javascript_functions_and_classes():
    _skip_if_no_parser("javascript")
    source = (
        "function add(a, b) { return a + b; }\n"
        "class Widget {\n"
        "  render() { return null; }\n"
        "}\n"
    )
    chunks = ast_chunker.chunk_code(source, "repo", "app.js", "javascript")
    assert chunks is not None
    by_name = _by_name(chunks)
    assert "add" in by_name
    assert by_name["add"].node_kind == "function"
    assert "Widget" in by_name
    assert by_name["Widget"].node_kind == "class"
    assert by_name["render"].parent_class == "Widget"
