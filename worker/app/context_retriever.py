"""Context retrieval layer.

Builds semantic queries from PR diff chunks and retrieves related code from the
vector store, packaging everything into structured bundles for downstream
consumers. Contains no OpenAI or LangGraph logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app import vector_store
from app.diff_processor import DiffChunk, DiffLine, ProcessedFile

logger = logging.getLogger("ai-code-review-worker.context")

# Diff line types whose content carries meaningful query signal.
_QUERY_LINE_TYPES = ("addition", "context")
_LINE_PREFIX = {"addition": "+", "deletion": "-", "context": " "}


@dataclass
class RetrievedContext:
    repository: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    content: str
    score: Optional[float]


@dataclass
class DiffContextBundle:
    repository: str
    diff_file_path: str
    chunk_index: int
    diff_text: str
    retrieved_contexts: list[RetrievedContext] = field(default_factory=list)


def _chunk_diff_text(diff_chunk: DiffChunk) -> str:
    """Reconstruct a readable unified-diff-style text for a chunk."""
    rendered: list[str] = []
    for line in diff_chunk.diff_lines:
        prefix = _LINE_PREFIX.get(line.line_type, " ")
        rendered.append(f"{prefix}{line.content}")
    return "\n".join(rendered)


def build_diff_query(diff_chunk: DiffChunk) -> str:
    """Build a semantic query string from a diff chunk.

    Prefers added and context lines (the post-change state of the code) and
    ignores deleted-only content. Includes the file path when available. Returns
    an empty string when the chunk has no useful signal.
    """
    parts: list[str] = []

    file_path = getattr(diff_chunk, "file_path", "") or ""
    if file_path:
        parts.append(f"File: {file_path}")

    useful_lines = [
        line.content
        for line in diff_chunk.diff_lines
        if line.line_type in _QUERY_LINE_TYPES and line.content.strip()
    ]
    parts.extend(useful_lines)

    query = "\n".join(parts).strip()
    logger.info(
        "Built diff query",
        extra={
            "file_path": file_path,
            "useful_lines": len(useful_lines),
            "query_empty": not bool(query),
        },
    )
    return query


def _map_results(results: dict[str, Any] | None) -> list[RetrievedContext]:
    """Convert a ChromaDB query result dict into RetrievedContext objects."""
    if not results:
        return []

    def _first(key: str) -> list[Any]:
        value = results.get(key)
        if not value:
            return []
        first = value[0]
        return list(first) if first is not None else []

    documents = _first("documents")
    metadatas = _first("metadatas")
    distances = _first("distances")

    contexts: list[RetrievedContext] = []
    for index, metadata in enumerate(metadatas):
        metadata = metadata or {}
        content = documents[index] if index < len(documents) else ""
        score = distances[index] if index < len(distances) else None
        contexts.append(
            RetrievedContext(
                repository=metadata.get("repository", ""),
                file_path=metadata.get("file_path", ""),
                language=metadata.get("language", "unknown"),
                start_line=metadata.get("start_line", 0),
                end_line=metadata.get("end_line", 0),
                content=content,
                score=score,
            )
        )
    return contexts


def retrieve_context_for_chunk(
    repository: str,
    diff_chunk: DiffChunk,
    top_k: int = 5,
) -> DiffContextBundle:
    """Retrieve related code context for a single diff chunk."""
    bundle = DiffContextBundle(
        repository=repository,
        diff_file_path=getattr(diff_chunk, "file_path", "") or "",
        chunk_index=getattr(diff_chunk, "chunk_index", 0),
        diff_text=_chunk_diff_text(diff_chunk),
        retrieved_contexts=[],
    )

    query = build_diff_query(diff_chunk)
    if not query:
        logger.info(
            "Skipping retrieval for empty query",
            extra={
                "repository": repository,
                "file_path": bundle.diff_file_path,
                "chunk_index": bundle.chunk_index,
            },
        )
        return bundle

    try:
        results = vector_store.query_similar_chunks(repository, query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on retrieval failure
        logger.warning(
            "Context retrieval failed",
            extra={
                "repository": repository,
                "file_path": bundle.diff_file_path,
                "chunk_index": bundle.chunk_index,
                "error": str(exc),
            },
        )
        return bundle

    bundle.retrieved_contexts = _map_results(results)
    if bundle.retrieved_contexts:
        logger.info(
            "Context retrieval succeeded",
            extra={
                "repository": repository,
                "file_path": bundle.diff_file_path,
                "chunk_index": bundle.chunk_index,
                "results": len(bundle.retrieved_contexts),
            },
        )
    else:
        logger.info(
            "Context retrieval returned no results",
            extra={
                "repository": repository,
                "file_path": bundle.diff_file_path,
                "chunk_index": bundle.chunk_index,
            },
        )
    return bundle


def retrieve_context_for_files(
    repository: str,
    processed_files: Sequence[ProcessedFile],
    top_k: int = 5,
) -> list[DiffContextBundle]:
    """Retrieve context for every chunk across a set of processed files."""
    bundles: list[DiffContextBundle] = []
    for processed_file in processed_files:
        for diff_chunk in processed_file.chunks:
            bundles.append(
                retrieve_context_for_chunk(repository, diff_chunk, top_k=top_k)
            )

    logger.info(
        "Completed context retrieval for files",
        extra={
            "repository": repository,
            "files": len(processed_files),
            "bundles": len(bundles),
        },
    )
    return bundles
