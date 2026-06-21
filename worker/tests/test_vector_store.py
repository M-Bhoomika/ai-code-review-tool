from unittest.mock import MagicMock

import pytest

from app import vector_store
from app.repository_indexer import CodeChunk


@pytest.fixture
def fake_collection(monkeypatch) -> MagicMock:
    collection = MagicMock()
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: client)
    return collection


def _chunk(chunk_id: str, start: int, end: int) -> CodeChunk:
    return CodeChunk(
        chunk_id=chunk_id,
        repository="octocat/hello",
        file_path="app/main.py",
        language="python",
        content=f"code-{chunk_id}",
        start_line=start,
        end_line=end,
    )


def test_store_chunks_persists_metadata(fake_collection):
    chunks = [_chunk("id1", 1, 200), _chunk("id2", 181, 380)]

    count = vector_store.store_chunks(chunks)

    assert count == 2
    fake_collection.add.assert_called_once()
    kwargs = fake_collection.add.call_args.kwargs

    assert kwargs["ids"] == ["id1", "id2"]
    assert kwargs["documents"] == ["code-id1", "code-id2"]
    assert kwargs["metadatas"][0] == {
        "repository": "octocat/hello",
        "file_path": "app/main.py",
        "language": "python",
        "start_line": 1,
        "end_line": 200,
        "function_name": "",
        "class_name": "",
    }
    assert kwargs["metadatas"][1]["start_line"] == 181
    assert kwargs["metadatas"][1]["end_line"] == 380


def test_store_chunks_includes_ast_metadata(fake_collection):
    method = CodeChunk(
        chunk_id="m1",
        repository="octocat/hello",
        file_path="app/svc.py",
        language="python",
        content="def handle(self): ...",
        start_line=10,
        end_line=20,
        symbol_name="handle",
        parent_class="Service",
        node_kind="function",
    )
    cls = CodeChunk(
        chunk_id="c1",
        repository="octocat/hello",
        file_path="app/svc.py",
        language="python",
        content="class Service: ...",
        start_line=1,
        end_line=40,
        symbol_name="Service",
        parent_class=None,
        node_kind="class",
    )

    vector_store.store_chunks([method, cls])

    metadatas = fake_collection.add.call_args.kwargs["metadatas"]
    assert metadatas[0]["function_name"] == "handle"
    assert metadatas[0]["class_name"] == "Service"
    # Class chunks expose their own name as class_name, not function_name.
    assert metadatas[1]["function_name"] == ""
    assert metadatas[1]["class_name"] == "Service"


def test_store_chunks_empty_is_noop(fake_collection):
    assert vector_store.store_chunks([]) == 0
    fake_collection.add.assert_not_called()


def test_query_similar_chunks_calls_collection(fake_collection):
    fake_collection.query.return_value = {"ids": [["id1", "id2"]]}

    result = vector_store.query_similar_chunks(
        "octocat/hello", "where is auth handled", top_k=5
    )

    fake_collection.query.assert_called_once_with(
        query_texts=["where is auth handled"],
        n_results=5,
        where={"repository": "octocat/hello"},
    )
    assert result == {"ids": [["id1", "id2"]]}


def test_query_similar_chunks_default_top_k(fake_collection):
    fake_collection.query.return_value = {"ids": [[]]}

    vector_store.query_similar_chunks("octocat/hello", "query text")

    kwargs = fake_collection.query.call_args.kwargs
    assert kwargs["n_results"] == 10


def test_get_collection_uses_get_or_create(fake_collection, monkeypatch):
    client = MagicMock()
    client.get_or_create_collection.return_value = fake_collection
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: client)

    vector_store.get_collection()

    client.get_or_create_collection.assert_called_once_with(
        name="code_chunks"
    )
