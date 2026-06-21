from unittest.mock import MagicMock

import pytest

from app import context_retriever
from app.context_retriever import (
    DiffContextBundle,
    RetrievedContext,
    build_diff_query,
    retrieve_context_for_chunk,
    retrieve_context_for_files,
)
from app.diff_processor import DiffChunk, DiffLine, ProcessedFile


def _line(content, line_type, position, old=None, new=None):
    return DiffLine(
        content=content,
        diff_position=position,
        line_type=line_type,
        old_line_number=old,
        new_line_number=new,
    )


def _chunk(file_path="app/main.py", chunk_index=0, lines=None):
    return DiffChunk(
        file_path=file_path,
        chunk_index=chunk_index,
        diff_lines=lines or [],
        token_estimate=0,
    )


def _sample_results():
    return {
        "ids": [["c1", "c2"]],
        "documents": [["def login():\n    ...", "def logout():\n    ..."]],
        "metadatas": [
            [
                {
                    "repository": "octocat/hello",
                    "file_path": "app/auth.py",
                    "language": "python",
                    "start_line": 1,
                    "end_line": 20,
                },
                {
                    "repository": "octocat/hello",
                    "file_path": "app/auth.py",
                    "language": "python",
                    "start_line": 21,
                    "end_line": 40,
                },
            ]
        ],
        "distances": [[0.12, 0.34]],
    }


def test_query_uses_added_and_context_lines_ignores_deletions():
    chunk = _chunk(
        file_path="app/auth.py",
        lines=[
            _line("def added_func():", "addition", 1, new=1),
            _line("context_line()", "context", 2, old=1, new=2),
            _line("removed_func()", "deletion", 3, old=2),
        ],
    )

    query = build_diff_query(chunk)

    assert "app/auth.py" in query
    assert "def added_func():" in query
    assert "context_line()" in query
    assert "removed_func()" not in query


def test_empty_chunk_produces_safe_empty_query():
    chunk = _chunk(file_path="", lines=[])
    assert build_diff_query(chunk) == ""


def test_empty_chunk_skips_vector_store(monkeypatch):
    mock_query = MagicMock()
    monkeypatch.setattr(vector_store_attr(), "query_similar_chunks", mock_query)

    chunk = _chunk(file_path="", lines=[])
    bundle = retrieve_context_for_chunk("octocat/hello", chunk)

    assert isinstance(bundle, DiffContextBundle)
    assert bundle.retrieved_contexts == []
    mock_query.assert_not_called()


def vector_store_attr():
    # Helper so monkeypatch targets the module object used inside the retriever.
    return context_retriever.vector_store


def test_query_called_with_repository_and_top_k(monkeypatch):
    mock_query = MagicMock(return_value=_sample_results())
    monkeypatch.setattr(vector_store_attr(), "query_similar_chunks", mock_query)

    chunk = _chunk(
        file_path="app/auth.py",
        lines=[_line("login()", "addition", 1, new=1)],
    )
    retrieve_context_for_chunk("octocat/hello", chunk, top_k=7)

    assert mock_query.call_count == 1
    args, kwargs = mock_query.call_args
    assert args[0] == "octocat/hello"
    assert kwargs["top_k"] == 7
    assert "login()" in args[1]


def test_retrieved_metadata_maps_correctly(monkeypatch):
    monkeypatch.setattr(
        vector_store_attr(),
        "query_similar_chunks",
        MagicMock(return_value=_sample_results()),
    )

    chunk = _chunk(
        file_path="app/auth.py",
        chunk_index=2,
        lines=[_line("login()", "addition", 1, new=1)],
    )
    bundle = retrieve_context_for_chunk("octocat/hello", chunk, top_k=5)

    assert bundle.diff_file_path == "app/auth.py"
    assert bundle.chunk_index == 2
    assert len(bundle.retrieved_contexts) == 2

    first = bundle.retrieved_contexts[0]
    assert isinstance(first, RetrievedContext)
    assert first.repository == "octocat/hello"
    assert first.file_path == "app/auth.py"
    assert first.language == "python"
    assert first.start_line == 1
    assert first.end_line == 20
    assert first.content == "def login():\n    ..."
    assert first.score == 0.12


def test_retrieval_failure_returns_empty_bundle(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(vector_store_attr(), "query_similar_chunks", boom)

    chunk = _chunk(
        file_path="app/auth.py",
        lines=[_line("login()", "addition", 1, new=1)],
    )
    bundle = retrieve_context_for_chunk("octocat/hello", chunk)

    assert bundle.retrieved_contexts == []
    assert bundle.diff_file_path == "app/auth.py"


def test_multiple_processed_files_produce_multiple_bundles(monkeypatch):
    monkeypatch.setattr(
        vector_store_attr(),
        "query_similar_chunks",
        MagicMock(return_value=_sample_results()),
    )

    file_a = ProcessedFile(
        file_path="a.py",
        additions=1,
        deletions=0,
        status="modified",
        chunks=[
            _chunk("a.py", 0, [_line("x()", "addition", 1, new=1)]),
            _chunk("a.py", 1, [_line("y()", "addition", 1, new=1)]),
        ],
    )
    file_b = ProcessedFile(
        file_path="b.py",
        additions=1,
        deletions=0,
        status="modified",
        chunks=[_chunk("b.py", 0, [_line("z()", "addition", 1, new=1)])],
    )

    bundles = retrieve_context_for_files(
        "octocat/hello", [file_a, file_b], top_k=5
    )

    assert len(bundles) == 3
    assert [b.diff_file_path for b in bundles] == ["a.py", "a.py", "b.py"]
