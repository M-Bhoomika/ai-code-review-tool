"""Publish AI-generated review comments to GitHub pull requests.

Consumes review comments produced by the worker's review engine (duck-typed:
any object exposing ``file_path``, ``line_number``, ``severity``, ``title``,
``explanation``, and ``suggestion``) and publishes them via PyGithub, either as
a single batched review or as individual review comments.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

logger = logging.getLogger("ai-code-review-api.review_publisher")


@dataclass
class GitHubReviewComment:
    path: str
    line: Optional[int]
    body: str


@dataclass
class PublishResult:
    comments_attempted: int
    comments_created: int
    comments_failed: int


def build_review_comment(comment: Any) -> GitHubReviewComment:
    """Convert a worker ReviewComment into a GitHubReviewComment."""
    severity = str(getattr(comment, "severity", "") or "info").strip() or "info"
    title = str(getattr(comment, "title", "") or "").strip()
    explanation = str(getattr(comment, "explanation", "") or "").strip()
    suggestion = str(getattr(comment, "suggestion", "") or "").strip()

    parts = [f"Severity: {severity}", title, explanation]
    if suggestion:
        parts.append(f"Suggestion:\n{suggestion}")
    body = "\n\n".join(parts)

    return GitHubReviewComment(
        path=str(getattr(comment, "file_path", "") or ""),
        line=getattr(comment, "line_number", None),
        body=body,
    )


def _get_pull_request(github_client: Any, repository: str, pull_number: int) -> Any:
    repo = github_client.get_repo(repository)
    return repo.get_pull(pull_number)


def _head_commit(pull_request: Any) -> Any:
    commits = list(pull_request.get_commits())
    return commits[-1] if commits else None


def publish_review_comments(
    github_client: Any,
    repository: str,
    pull_number: int,
    review_comments: Sequence[Any],
) -> PublishResult:
    """Publish each review comment as an individual GitHub review comment.

    Continues on per-comment failures and returns aggregate counts.
    """
    gh_comments = [build_review_comment(comment) for comment in review_comments]
    result = PublishResult(
        comments_attempted=len(gh_comments),
        comments_created=0,
        comments_failed=0,
    )

    logger.info(
        "Publishing individual review comments started",
        extra={
            "repository": repository,
            "pull_number": pull_number,
            "attempted": result.comments_attempted,
        },
    )

    if not gh_comments:
        return result

    try:
        pull_request = _get_pull_request(github_client, repository, pull_number)
        commit = _head_commit(pull_request)
    except Exception as exc:  # noqa: BLE001 - mark all as failed and report
        logger.warning(
            "Failed to load pull request for publishing",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "error": str(exc),
            },
        )
        result.comments_failed = len(gh_comments)
        return result

    for gh_comment in gh_comments:
        try:
            pull_request.create_review_comment(
                body=gh_comment.body,
                commit=commit,
                path=gh_comment.path,
                line=gh_comment.line,
            )
            result.comments_created += 1
            logger.info(
                "Published review comment",
                extra={
                    "repository": repository,
                    "pull_number": pull_number,
                    "path": gh_comment.path,
                    "line": gh_comment.line,
                },
            )
        except Exception as exc:  # noqa: BLE001 - continue on failure
            result.comments_failed += 1
            logger.warning(
                "Failed to publish review comment",
                extra={
                    "repository": repository,
                    "pull_number": pull_number,
                    "path": gh_comment.path,
                    "line": gh_comment.line,
                    "error": str(exc),
                },
            )

    logger.info(
        "Publishing individual review comments completed",
        extra={
            "repository": repository,
            "pull_number": pull_number,
            "created": result.comments_created,
            "failed": result.comments_failed,
        },
    )
    return result


def publish_batch_review(
    github_client: Any,
    repository: str,
    pull_number: int,
    review_comments: Sequence[Any],
) -> PublishResult:
    """Publish all comments in a single GitHub review.

    Falls back to individual publishing if the batched review fails.
    """
    gh_comments = [build_review_comment(comment) for comment in review_comments]
    attempted = len(gh_comments)

    logger.info(
        "Publishing batch review started",
        extra={
            "repository": repository,
            "pull_number": pull_number,
            "attempted": attempted,
        },
    )

    if not gh_comments:
        return PublishResult(
            comments_attempted=0, comments_created=0, comments_failed=0
        )

    try:
        pull_request = _get_pull_request(github_client, repository, pull_number)
        commit = _head_commit(pull_request)
        payload = [
            {"path": gh.path, "line": gh.line, "body": gh.body}
            for gh in gh_comments
        ]
        pull_request.create_review(
            commit=commit,
            body="Automated AI code review",
            event="COMMENT",
            comments=payload,
        )
        logger.info(
            "Published batch review",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "created": attempted,
            },
        )
        return PublishResult(
            comments_attempted=attempted,
            comments_created=attempted,
            comments_failed=0,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to individual publishing
        logger.warning(
            "Batch review failed; falling back to individual comments",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "error": str(exc),
            },
        )
        return publish_review_comments(
            github_client, repository, pull_number, review_comments
        )
