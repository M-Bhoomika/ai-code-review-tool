"""High-level GitHub client service for the worker.

Wraps an authenticated PyGithub client and exposes the operations the review
pipeline needs: loading repositories, pull requests, commits, and changed
files, and publishing review comments. ``raw`` exposes the underlying PyGithub
client so existing pipeline interfaces (which call ``get_repo``/``get_pull``)
remain usable unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from github import Github

from app.github.auth import create_github_client

logger = logging.getLogger("ai-code-review-worker.github")


class GitHubClient:
    def __init__(self, client: Github):
        self._client = client

    @classmethod
    def from_env(cls) -> "GitHubClient":
        """Build a GitHubClient using the token from the environment."""
        return cls(create_github_client())

    @property
    def raw(self) -> Github:
        """Return the underlying authenticated PyGithub client."""
        return self._client

    def get_repository(self, repository: str) -> Any:
        logger.info("github_get_repository", extra={"repository": repository})
        return self._client.get_repo(repository)

    def get_pull_request(self, repository: str, pull_number: int) -> Any:
        logger.info(
            "github_get_pull_request",
            extra={"repository": repository, "pull_number": pull_number},
        )
        return self.get_repository(repository).get_pull(pull_number)

    def get_commits(self, repository: str, pull_number: int) -> list[Any]:
        pull_request = self.get_pull_request(repository, pull_number)
        return list(pull_request.get_commits())

    def get_changed_files(self, repository: str, pull_number: int) -> list[Any]:
        pull_request = self.get_pull_request(repository, pull_number)
        return list(pull_request.get_files())

    def _head_commit(self, pull_request: Any) -> Any:
        commits = list(pull_request.get_commits())
        return commits[-1] if commits else None

    def publish_review_comment(
        self,
        repository: str,
        pull_number: int,
        body: str,
        path: str,
        line: Optional[int],
        commit: Any = None,
    ) -> Any:
        """Publish a single line-level review comment."""
        pull_request = self.get_pull_request(repository, pull_number)
        commit = commit or self._head_commit(pull_request)
        result = pull_request.create_review_comment(
            body=body, commit=commit, path=path, line=line
        )
        logger.info(
            "github_review_comment_published",
            extra={"repository": repository, "pull_number": pull_number, "path": path},
        )
        return result

    def publish_review(
        self,
        repository: str,
        pull_number: int,
        comments: Sequence[dict],
        body: str = "Automated AI code review",
        event: str = "COMMENT",
    ) -> Any:
        """Publish a batched review containing multiple comments."""
        pull_request = self.get_pull_request(repository, pull_number)
        commit = self._head_commit(pull_request)
        result = pull_request.create_review(
            commit=commit,
            body=body,
            event=event,
            comments=list(comments),
        )
        logger.info(
            "github_review_published",
            extra={
                "repository": repository,
                "pull_number": pull_number,
                "comments": len(comments),
            },
        )
        return result
