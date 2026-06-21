"""LLM review engine.

Builds review prompts from diff context bundles, sends them through an injected
LLM client, and parses structured responses into review comments. The LLM
client is dependency-injected (no direct OpenAI integration here); it only needs
to expose ``complete(prompt: str) -> str``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, Sequence

if TYPE_CHECKING:
    from app.context_retriever import DiffContextBundle, RetrievedContext

logger = logging.getLogger("ai-code-review-worker.review")

REVIEW_CATEGORIES = (
    "bugs",
    "security issues",
    "performance issues",
    "maintainability issues",
    "code quality issues",
)


class LLMClient(Protocol):
    """Minimal contract for an injected LLM client."""

    def complete(self, prompt: str) -> str:  # pragma: no cover - interface only
        ...


@dataclass
class ReviewComment:
    file_path: str
    line_number: Optional[int]
    severity: str
    title: str
    explanation: str
    suggestion: str


@dataclass
class ReviewResult:
    repository: str
    total_comments: int
    comments: list[ReviewComment] = field(default_factory=list)
    security_findings: int = 0
    performance_findings: int = 0
    logic_findings: int = 0


def build_review_prompt(
    diff_text: str,
    retrieved_contexts: "Sequence[RetrievedContext] | None",
) -> str:
    """Construct a structured review prompt from a diff and related context."""
    sections: list[str] = []
    for ctx in retrieved_contexts or []:
        sections.append(
            f"--- {ctx.file_path} "
            f"(lines {ctx.start_line}-{ctx.end_line}, {ctx.language}) ---\n"
            f"{ctx.content}"
        )
    context_block = (
        "\n\n".join(sections) if sections else "(no related context found)"
    )

    categories = "\n".join(f"- {category}" for category in REVIEW_CATEGORIES)

    prompt = f"""You are an expert code reviewer. Review the following pull \
request diff and identify concrete, actionable issues.

Focus on these categories:
{categories}

## Diff under review
{diff_text}

## Related repository context
{context_block}

## Response format
Respond ONLY with a JSON array. Each element must be an object with the keys:
- "file_path": string
- "line_number": integer (the affected line, or null)
- "severity": one of "critical", "high", "medium", "low", "info"
- "title": short summary of the issue
- "explanation": why it matters
- "suggestion": how to fix it

If there are no issues, respond with an empty JSON array: []
Do not include any prose outside the JSON array."""

    logger.info(
        "Built review prompt",
        extra={
            "diff_chars": len(diff_text or ""),
            "context_snippets": len(sections),
        },
    )
    return prompt


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _safe_json_load(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    # Fall back to extracting the outermost JSON array/object.
    for open_char, close_char in (("[", "]"), ("{", "}")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (ValueError, TypeError):
                continue
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_review_response(response_text: str) -> list[ReviewComment]:
    """Parse a structured LLM response into ReviewComment objects.

    Resilient to fenced/malformed output; incomplete entries (missing
    file_path, title, or explanation) are ignored.
    """
    if not response_text or not response_text.strip():
        return []

    data = _safe_json_load(_strip_code_fences(response_text))
    if data is None:
        logger.warning("Failed to parse review response as JSON")
        return []

    if isinstance(data, dict):
        entries = data.get("comments")
    else:
        entries = data

    if not isinstance(entries, list):
        logger.warning("Review response did not contain a list of comments")
        return []

    comments: list[ReviewComment] = []
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue

        file_path = str(entry.get("file_path", "") or "").strip()
        title = str(entry.get("title", "") or "").strip()
        explanation = str(entry.get("explanation", "") or "").strip()

        if not file_path or not title or not explanation:
            skipped += 1
            continue

        severity = str(entry.get("severity", "info") or "info").strip() or "info"
        comments.append(
            ReviewComment(
                file_path=file_path,
                line_number=_coerce_int(entry.get("line_number")),
                severity=severity,
                title=title,
                explanation=explanation,
                suggestion=str(entry.get("suggestion", "") or "").strip(),
            )
        )

    if skipped:
        logger.info(
            "Ignored incomplete review entries", extra={"skipped": skipped}
        )
    return comments


def generate_review(
    repository: str,
    diff_bundle: "DiffContextBundle",
    llm_client: LLMClient,
) -> ReviewResult:
    """Generate a review for a single diff context bundle."""
    if llm_client is None:
        logger.info(
            "LLM client unavailable; skipping review generation",
            extra={
                "repository": repository,
                "file_path": diff_bundle.diff_file_path,
            },
        )
        return ReviewResult(repository=repository, total_comments=0, comments=[])

    prompt = build_review_prompt(
        diff_bundle.diff_text, diff_bundle.retrieved_contexts
    )

    logger.info(
        "Requesting LLM review",
        extra={
            "repository": repository,
            "file_path": diff_bundle.diff_file_path,
            "chunk_index": diff_bundle.chunk_index,
        },
    )

    try:
        response_text = llm_client.complete(prompt)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on LLM failure
        logger.warning(
            "LLM review request failed",
            extra={
                "repository": repository,
                "file_path": diff_bundle.diff_file_path,
                "chunk_index": diff_bundle.chunk_index,
                "error": str(exc),
            },
        )
        return ReviewResult(repository=repository, total_comments=0, comments=[])

    comments = parse_review_response(response_text)
    logger.info(
        "LLM review completed",
        extra={
            "repository": repository,
            "file_path": diff_bundle.diff_file_path,
            "chunk_index": diff_bundle.chunk_index,
            "comments": len(comments),
        },
    )
    return ReviewResult(
        repository=repository,
        total_comments=len(comments),
        comments=comments,
    )


def generate_reviews_for_bundles(
    repository: str,
    bundles: "Sequence[DiffContextBundle]",
    llm_client: LLMClient,
) -> ReviewResult:
    """Generate and aggregate reviews across multiple diff context bundles."""
    all_comments: list[ReviewComment] = []
    for bundle in bundles:
        result = generate_review(repository, bundle, llm_client)
        all_comments.extend(result.comments)

    logger.info(
        "Aggregated reviews for bundles",
        extra={
            "repository": repository,
            "bundles": len(bundles),
            "total_comments": len(all_comments),
        },
    )
    return ReviewResult(
        repository=repository,
        total_comments=len(all_comments),
        comments=all_comments,
    )
