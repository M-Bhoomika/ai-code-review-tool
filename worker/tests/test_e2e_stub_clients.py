import json
import os

import pytest

from app.e2e.stub_clients import (
    E2E_PULL_NUMBER,
    E2E_REPOSITORY,
    StubGitHubClient,
    StubLLMClient,
    build_stub_github_client,
    build_stub_llm_client,
    e2e_integration_enabled,
)
from app.review_engine import parse_review_response


def test_stub_github_client_returns_reviewable_diff():
    client = build_stub_github_client(0)
    repo = client.get_repo(E2E_REPOSITORY)
    pull = repo.get_pull(E2E_PULL_NUMBER)
    files = list(pull.get_files())

    assert len(files) == 1
    assert files[0].filename == "app/main.py"
    assert files[0].patch


def test_stub_llm_client_returns_structured_comments():
    client = build_stub_llm_client()
    comments = parse_review_response(client.complete("review this diff"))

    assert len(comments) == 1
    assert comments[0].title == "Possible null return"


def test_e2e_integration_flag(monkeypatch):
    monkeypatch.delenv("E2E_INTEGRATION", raising=False)
    assert e2e_integration_enabled() is False

    monkeypatch.setenv("E2E_INTEGRATION", "true")
    assert e2e_integration_enabled() is True


def test_build_clients_use_stub_when_flag_set(monkeypatch):
    monkeypatch.setenv("E2E_INTEGRATION", "true")
    from app.clients import build_github_client, build_llm_client

    github = build_github_client(0)
    llm = build_llm_client()

    assert isinstance(github, StubGitHubClient)
    assert isinstance(llm, StubLLMClient)
    assert json.loads(llm.complete("x"))
