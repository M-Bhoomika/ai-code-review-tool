"""Deterministic GitHub and LLM stand-ins for end-to-end integration runs.

Activated only when ``E2E_INTEGRATION=true``. External GitHub and OpenAI are
replaced so the real worker pipeline, PostgreSQL persistence, and GraphQL
analytics path can be exercised without network credentials.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

E2E_REPOSITORY = "octocat/hello"
E2E_PULL_NUMBER = 42
E2E_COMMIT_SHA = "e2e-integration-commit-sha"

_SAMPLE_PATCH = """@@ -1,3 +1,4 @@
 def hello():
-    return 1
+    return None
"""


def e2e_integration_enabled() -> bool:
    return os.getenv("E2E_INTEGRATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class _StubPullFile:
    filename = "app/main.py"
    additions = 1
    deletions = 1
    status = "modified"
    patch = _SAMPLE_PATCH


class _StubPullRequest:
    def __init__(self, pull_number: int) -> None:
        self.number = pull_number
        self.head = SimpleNamespace(sha=E2E_COMMIT_SHA)

    def get_files(self) -> list[_StubPullFile]:
        return [_StubPullFile()]

    def get_commits(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(sha=E2E_COMMIT_SHA)]

    def create_review(self, **_kwargs: Any) -> None:
        return None

    def create_review_comment(self, **_kwargs: Any) -> None:
        return None


class _StubRepository:
    name = "hello"
    id = 42424242
    default_branch = "main"
    owner = SimpleNamespace(login="octocat")

    def get_pull(self, pull_number: int) -> _StubPullRequest:
        return _StubPullRequest(pull_number)


class StubGitHubClient:
    """Minimal PyGithub-shaped client for integration testing."""

    def get_repo(self, _repository: str) -> _StubRepository:
        return _StubRepository()


class StubLLMClient:
    """Returns a fixed structured review comment for every prompt."""

    model = "e2e-stub"

    def complete(self, _prompt: str) -> str:
        return json.dumps(
            [
                {
                    "file_path": "app/main.py",
                    "line_number": 2,
                    "severity": "high",
                    "title": "Possible null return",
                    "explanation": "The function may return None unexpectedly.",
                    "suggestion": "Return a concrete default value.",
                }
            ]
        )


def build_stub_github_client(_installation_id: int = 0) -> StubGitHubClient:
    return StubGitHubClient()


def build_stub_llm_client() -> StubLLMClient:
    return StubLLMClient()
