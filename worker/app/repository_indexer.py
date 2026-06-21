"""Repository indexing engine.

Recursively scans a checked-out repository, detects languages, splits source
files into overlapping line-based chunks, and (optionally) persists them to the
vector store. Contains no OpenAI or LangGraph logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app import ast_chunker, vector_store

logger = logging.getLogger("ai-code-review-worker.indexer")

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".c": "c",
    ".cs": "csharp",
}

SKIP_DIRECTORIES: set[str] = {
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".venv",
    "__pycache__",
}

MAX_FILE_SIZE_BYTES = 1024 * 1024  # 1 MB
CHUNK_TARGET_LINES = 200
CHUNK_OVERLAP_LINES = 20
_BINARY_SNIFF_BYTES = 1024


@dataclass
class CodeChunk:
    chunk_id: str
    repository: str
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    # Populated by AST-aware chunking; None for line-based chunks.
    symbol_name: Optional[str] = None
    parent_class: Optional[str] = None
    node_kind: Optional[str] = None  # "class" | "function"


@dataclass
class IndexingResult:
    repository: str
    files_processed: int
    chunks_created: int


def detect_language(file_path: str) -> str:
    """Return the language for a file based on its extension, else 'unknown'."""
    return LANGUAGE_BY_EXTENSION.get(Path(file_path).suffix.lower(), "unknown")


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True


def should_index_file(file_path: str) -> bool:
    """Decide whether a file should be indexed.

    Skips excluded directories, files over the size limit, and binary files.
    """
    path = Path(file_path)

    if any(part in SKIP_DIRECTORIES for part in path.parts):
        return False

    if path.is_file():
        try:
            if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                return False
        except OSError:
            return False
        if _is_binary(path):
            return False

    return True


def _make_chunk_id(
    repository: str, file_path: str, start_line: int, end_line: int
) -> str:
    return f"{repository}:{file_path}:{start_line}-{end_line}"


def chunk_source_file(
    content: str,
    repository: str = "",
    file_path: str = "",
    language: str = "unknown",
) -> list[CodeChunk]:
    """Split file content into overlapping, line-numbered chunks.

    Chunks target ~200 lines with a 20-line overlap; 1-based line numbers are
    preserved on each chunk.
    """
    lines = content.splitlines()
    if not lines:
        return []

    step = CHUNK_TARGET_LINES - CHUNK_OVERLAP_LINES  # advance per chunk
    chunks: list[CodeChunk] = []
    start = 0  # 0-based index into ``lines``

    while start < len(lines):
        end = min(start + CHUNK_TARGET_LINES, len(lines))
        start_line = start + 1  # 1-based, inclusive
        end_line = end  # 1-based, inclusive
        chunk_content = "\n".join(lines[start:end])

        chunks.append(
            CodeChunk(
                chunk_id=_make_chunk_id(
                    repository, file_path, start_line, end_line
                ),
                repository=repository,
                file_path=file_path,
                language=language,
                content=chunk_content,
                start_line=start_line,
                end_line=end_line,
            )
        )

        if end == len(lines):
            break
        start += step

    return chunks


def index_repository(
    repository_path: str,
    repository_name: str,
    persist: bool = True,
) -> IndexingResult:
    """Scan a repository, chunk its source files, and optionally persist them.

    When ``persist`` is True the collected chunks are written to the vector
    store. Returns an :class:`IndexingResult` with file and chunk counts.
    """
    root = Path(repository_path)
    files_processed = 0
    all_chunks: list[CodeChunk] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if not should_index_file(str(path)):
            continue

        relative_path = path.relative_to(root).as_posix()
        language = detect_language(relative_path)
        if language == "unknown":
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            logger.warning(
                "Failed to read file", extra={"file_path": relative_path}
            )
            continue

        # Prefer AST-aware chunking for supported languages; fall back to
        # line-based chunking for unsupported languages, missing grammars,
        # parse failures, or files with no extractable symbols.
        chunks = ast_chunker.chunk_code(
            content,
            repository=repository_name,
            file_path=relative_path,
            language=language,
        )
        if chunks is None:
            chunks = chunk_source_file(
                content,
                repository=repository_name,
                file_path=relative_path,
                language=language,
            )
        if not chunks:
            continue

        all_chunks.extend(chunks)
        files_processed += 1
        logger.info(
            "Indexed file",
            extra={
                "file_path": relative_path,
                "language": language,
                "chunks": len(chunks),
            },
        )

    if persist and all_chunks:
        vector_store.store_chunks(all_chunks)

    result = IndexingResult(
        repository=repository_name,
        files_processed=files_processed,
        chunks_created=len(all_chunks),
    )
    logger.info(
        "Completed repository indexing",
        extra={
            "repository": repository_name,
            "files_processed": result.files_processed,
            "chunks_created": result.chunks_created,
            "persisted": persist,
        },
    )
    return result
