"""PR diff processing engine.

Parses GitHub unified diffs into structured, position-aware representations and
splits large diffs into token-bounded chunks. This module is intentionally free
of any LLM logic; it only normalizes diffs for downstream consumers.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

logger = logging.getLogger("ai-code-review-worker.diff")

LineType = Literal["addition", "deletion", "context"]

# File extensions that carry little review value or are not human-reviewable.
SKIPPED_EXTENSIONS: tuple[str, ...] = (
    ".lock",
    ".json",
    ".md",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
)

# Files with more added lines than this are skipped (likely generated/vendored).
MAX_ADDED_LINES = 1000

# Maximum estimated tokens per chunk before splitting on hunk boundaries.
MAX_CHUNK_TOKENS = 3000

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


@dataclass
class DiffLine:
    content: str
    diff_position: int
    line_type: LineType
    old_line_number: Optional[int]
    new_line_number: Optional[int]


@dataclass
class DiffChunk:
    file_path: str
    chunk_index: int
    diff_lines: list[DiffLine]
    token_estimate: int


@dataclass
class ProcessedFile:
    file_path: str
    additions: int
    deletions: int
    status: str
    chunks: list[DiffChunk] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Approximate token count using a simple chars-per-token heuristic."""
    return len(text) // 4


def should_skip_file(file_path: str) -> bool:
    """Return True if a file should be excluded from review by its extension."""
    return file_path.lower().endswith(SKIPPED_EXTENSIONS)


def _parse(patch: Optional[str]) -> list[tuple[int, DiffLine]]:
    """Parse a unified diff into ``(hunk_index, DiffLine)`` tuples.

    GitHub review ``position`` counts every line after the first ``@@`` hunk
    header (starting at 1), and subsequent hunk headers also consume a position.
    """
    parsed: list[tuple[int, DiffLine]] = []
    if not patch:
        return parsed

    position: Optional[int] = None
    hunk_index = -1
    old_line = 0
    new_line = 0

    for raw in patch.splitlines():
        header = _HUNK_HEADER_RE.match(raw)
        if header:
            old_line = int(header.group(1))
            new_line = int(header.group(3))
            hunk_index += 1
            if position is None:
                position = 0
            else:
                # A subsequent hunk header occupies a diff position.
                position += 1
            continue

        if position is None:
            # Skip any preamble before the first hunk header.
            continue

        position += 1

        if raw.startswith("+"):
            parsed.append(
                (
                    hunk_index,
                    DiffLine(
                        content=raw[1:],
                        diff_position=position,
                        line_type="addition",
                        old_line_number=None,
                        new_line_number=new_line,
                    ),
                )
            )
            new_line += 1
        elif raw.startswith("-"):
            parsed.append(
                (
                    hunk_index,
                    DiffLine(
                        content=raw[1:],
                        diff_position=position,
                        line_type="deletion",
                        old_line_number=old_line,
                        new_line_number=None,
                    ),
                )
            )
            old_line += 1
        elif raw.startswith("\\"):
            # e.g. "\ No newline at end of file" — counts as a position but is
            # not a real source line, so it carries no line numbers.
            parsed.append(
                (
                    hunk_index,
                    DiffLine(
                        content=raw,
                        diff_position=position,
                        line_type="context",
                        old_line_number=None,
                        new_line_number=None,
                    ),
                )
            )
        else:
            content = raw[1:] if raw.startswith(" ") else raw
            parsed.append(
                (
                    hunk_index,
                    DiffLine(
                        content=content,
                        diff_position=position,
                        line_type="context",
                        old_line_number=old_line,
                        new_line_number=new_line,
                    ),
                )
            )
            old_line += 1
            new_line += 1

    return parsed


def parse_unified_diff(patch: Optional[str]) -> list[DiffLine]:
    """Parse a GitHub unified diff patch into an ordered list of DiffLines."""
    return [line for _, line in _parse(patch)]


def calculate_diff_positions(diff_lines: Iterable[DiffLine]) -> dict[int, int]:
    """Map new-file line numbers to GitHub review positions.

    Only lines that exist in the new file (additions and context) are valid
    comment anchors, so deletions are excluded from the mapping.
    """
    positions: dict[int, int] = {}
    for line in diff_lines:
        if line.new_line_number is not None and line.line_type in (
            "addition",
            "context",
        ):
            positions[line.new_line_number] = line.diff_position
    return positions


def _group_hunks(parsed: list[tuple[int, DiffLine]]) -> list[list[DiffLine]]:
    hunks: dict[int, list[DiffLine]] = {}
    for hunk_index, line in parsed:
        hunks.setdefault(hunk_index, []).append(line)
    return [hunks[key] for key in sorted(hunks)]


def chunk_large_diff(
    file_path: str,
    patch: Optional[str],
    max_tokens: int = MAX_CHUNK_TOKENS,
) -> list[DiffChunk]:
    """Split a diff into chunks, never breaking inside a hunk.

    Hunks are greedily packed into a chunk until adding the next hunk would
    exceed ``max_tokens``. A single hunk larger than ``max_tokens`` becomes its
    own chunk (it is never split internally).
    """
    hunks = _group_hunks(_parse(patch))

    chunks: list[DiffChunk] = []
    current_lines: list[DiffLine] = []
    current_tokens = 0
    chunk_index = 0

    for hunk in hunks:
        hunk_text = "\n".join(line.content for line in hunk)
        hunk_tokens = estimate_tokens(hunk_text)

        if current_lines and current_tokens + hunk_tokens > max_tokens:
            chunks.append(
                DiffChunk(
                    file_path=file_path,
                    chunk_index=chunk_index,
                    diff_lines=current_lines,
                    token_estimate=current_tokens,
                )
            )
            chunk_index += 1
            current_lines = []
            current_tokens = 0

        current_lines.extend(hunk)
        current_tokens += hunk_tokens

    if current_lines:
        chunks.append(
            DiffChunk(
                file_path=file_path,
                chunk_index=chunk_index,
                diff_lines=current_lines,
                token_estimate=current_tokens,
            )
        )

    if len(chunks) > 1:
        logger.info(
            "Split diff into multiple chunks",
            extra={"file_path": file_path, "chunk_count": len(chunks)},
        )

    return chunks


def fetch_pr_files(
    github_client,
    repository_name: str,
    pr_number: int,
) -> list[ProcessedFile]:
    """Fetch and normalize the changed files of a pull request via PyGithub.

    Skips files by extension, binary files (no patch), and files exceeding the
    maximum added-line threshold.
    """
    repo = github_client.get_repo(repository_name)
    pull_request = repo.get_pull(pr_number)

    processed_files: list[ProcessedFile] = []

    for gh_file in pull_request.get_files():
        file_path = gh_file.filename

        if should_skip_file(file_path):
            logger.info(
                "Skipping file by extension",
                extra={"file_path": file_path},
            )
            continue

        additions = getattr(gh_file, "additions", 0) or 0
        if additions > MAX_ADDED_LINES:
            logger.info(
                "Skipping file with too many additions",
                extra={"file_path": file_path, "additions": additions},
            )
            continue

        patch = getattr(gh_file, "patch", None)
        if not patch:
            # Binary files (and some very large diffs) have no patch text.
            logger.info(
                "Skipping binary or patchless file",
                extra={"file_path": file_path},
            )
            continue

        chunks = chunk_large_diff(file_path, patch)
        processed_files.append(
            ProcessedFile(
                file_path=file_path,
                additions=additions,
                deletions=getattr(gh_file, "deletions", 0) or 0,
                status=getattr(gh_file, "status", "modified"),
                chunks=chunks,
            )
        )
        logger.info(
            "Processed file",
            extra={
                "file_path": file_path,
                "additions": additions,
                "chunk_count": len(chunks),
            },
        )

    logger.info(
        "Completed PR file processing",
        extra={
            "repository": repository_name,
            "pr_number": pr_number,
            "processed_files": len(processed_files),
        },
    )
    return processed_files
