"""ChromaDB integration for storing and querying indexed code chunks.

The ChromaDB client is imported lazily inside :func:`get_chroma_client` so this
module (and the indexer that depends on it) can be imported and unit-tested
without the ``chromadb`` package or a running ChromaDB server.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Sequence
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.repository_indexer import CodeChunk

logger = logging.getLogger("ai-code-review-worker.vector_store")

COLLECTION_NAME = "code_chunks"
CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8001")

_client: Any | None = None


def _parse_host_port(url: str) -> tuple[str, int, bool]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    use_ssl = parsed.scheme == "https"
    port = parsed.port or (443 if use_ssl else 8000)
    return host, port, use_ssl


def get_chroma_client() -> Any:
    """Return a lazily-initialized ChromaDB HTTP client (one per process)."""
    global _client
    if _client is None:
        import chromadb  # imported lazily; see module docstring

        host, port, use_ssl = _parse_host_port(CHROMA_URL)
        logger.info(
            "Connecting to ChromaDB",
            extra={"host": host, "port": port, "ssl": use_ssl},
        )
        _client = chromadb.HttpClient(host=host, port=port, ssl=use_ssl)
    return _client


def get_collection(client: Any | None = None) -> Any:
    """Return (creating if needed) the ``code_chunks`` collection."""
    client = client or get_chroma_client()
    return client.get_or_create_collection(name=COLLECTION_NAME)


def _build_metadata(chunk: "CodeChunk") -> dict[str, Any]:
    """Build ChromaDB metadata for a chunk.

    ChromaDB rejects ``None`` metadata values, so symbol fields are coerced to
    empty strings. For class chunks the symbol name is exposed as ``class_name``;
    for functions/methods it is the ``function_name`` and the enclosing class (if
    any) is the ``class_name``.
    """
    node_kind = getattr(chunk, "node_kind", None)
    symbol_name = getattr(chunk, "symbol_name", None) or ""
    parent_class = getattr(chunk, "parent_class", None) or ""

    if node_kind == "class":
        function_name = ""
        class_name = symbol_name
    else:
        function_name = symbol_name
        class_name = parent_class

    return {
        "repository": chunk.repository,
        "file_path": chunk.file_path,
        "language": chunk.language,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "function_name": function_name,
        "class_name": class_name,
    }


def store_chunks(chunks: "Sequence[CodeChunk]") -> int:
    """Persist code chunks and their metadata into ChromaDB.

    Returns the number of chunks stored.
    """
    if not chunks:
        logger.info("store_chunks called with no chunks")
        return 0

    collection = get_collection()

    ids = [chunk.chunk_id for chunk in chunks]
    documents = [chunk.content for chunk in chunks]
    metadatas = [_build_metadata(chunk) for chunk in chunks]

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    logger.info(
        "Stored code chunks in ChromaDB",
        extra={"count": len(ids), "collection": COLLECTION_NAME},
    )
    return len(ids)


def query_similar_chunks(
    repository: str, query: str, top_k: int = 10
) -> dict[str, Any]:
    """Query the most similar chunks within a repository."""
    collection = get_collection()
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where={"repository": repository},
    )
    logger.info(
        "Queried similar code chunks",
        extra={"repository": repository, "top_k": top_k},
    )
    return results
