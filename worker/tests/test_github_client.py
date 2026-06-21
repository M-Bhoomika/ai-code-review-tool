from unittest.mock import MagicMock

import pytest

from app import clients
from app.github import auth
from app.github.client import GitHubClient


# --- auth: token loading ---


def test_get_github_token_reads_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
    assert auth.get_github_token() == "ghp_abc"


def test_get_github_token_blank_is_none(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert auth.get_github_token() is None


def test_get_github_token_unset_is_none(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert auth.get_github_token() is None


# --- auth: client creation ---


def test_create_github_client_with_token(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    fake_github = MagicMock(return_value="GH_CLIENT")
    fake_token = MagicMock(return_value="AUTH_OBJ")
    monkeypatch.setattr(auth, "Github", fake_github)
    monkeypatch.setattr(auth.Auth, "Token", fake_token)

    client = auth.create_github_client("tok123")

    fake_token.assert_called_once_with("tok123")
    fake_github.assert_called_once_with(auth="AUTH_OBJ")
    assert client == "GH_CLIENT"


def test_create_github_client_uses_base_url(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    fake_github = MagicMock(return_value="GH_CLIENT")
    monkeypatch.setattr(auth, "Github", fake_github)
    monkeypatch.setattr(auth.Auth, "Token", MagicMock(return_value="AUTH_OBJ"))

    auth.create_github_client("tok123")

    _, kwargs = fake_github.call_args
    assert kwargs["base_url"] == "https://ghe.example.com/api/v3"
    assert kwargs["auth"] == "AUTH_OBJ"


def test_create_github_client_without_token_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        auth.create_github_client()


# --- GitHubClient service ---


def _service():
    raw = MagicMock(name="github")
    return GitHubClient(raw), raw


def test_service_get_repository():
    svc, raw = _service()
    svc.get_repository("octocat/hello")
    raw.get_repo.assert_called_once_with("octocat/hello")


def test_service_get_pull_request():
    svc, raw = _service()
    svc.get_pull_request("octocat/hello", 7)
    raw.get_repo.assert_called_once_with("octocat/hello")
    raw.get_repo.return_value.get_pull.assert_called_once_with(7)


def test_service_get_commits_returns_list():
    svc, raw = _service()
    pr = raw.get_repo.return_value.get_pull.return_value
    pr.get_commits.return_value = ["c1", "c2"]

    commits = svc.get_commits("octocat/hello", 7)
    assert commits == ["c1", "c2"]


def test_service_get_changed_files_returns_list():
    svc, raw = _service()
    pr = raw.get_repo.return_value.get_pull.return_value
    pr.get_files.return_value = ["f1"]

    files = svc.get_changed_files("octocat/hello", 7)
    assert files == ["f1"]


def test_service_publish_review_comment_uses_head_commit():
    svc, raw = _service()
    pr = raw.get_repo.return_value.get_pull.return_value
    pr.get_commits.return_value = ["c1", "c2"]

    svc.publish_review_comment(
        "octocat/hello", 7, body="b", path="app/main.py", line=12
    )

    pr.create_review_comment.assert_called_once_with(
        body="b", commit="c2", path="app/main.py", line=12
    )


def test_service_publish_review_batches_comments():
    svc, raw = _service()
    pr = raw.get_repo.return_value.get_pull.return_value
    pr.get_commits.return_value = ["c1"]
    payload = [{"path": "a.py", "line": 1, "body": "x"}]

    svc.publish_review("octocat/hello", 7, payload)

    pr.create_review.assert_called_once()
    kwargs = pr.create_review.call_args.kwargs
    assert kwargs["event"] == "COMMENT"
    assert kwargs["commit"] == "c1"
    assert kwargs["comments"] == payload


def test_service_from_env(monkeypatch):
    monkeypatch.setattr(
        "app.github.client.create_github_client",
        MagicMock(return_value="GH"),
    )
    svc = GitHubClient.from_env()
    assert svc.raw == "GH"


# --- build_github_client wiring ---


def test_build_github_client_returns_none_without_token(monkeypatch):
    monkeypatch.setattr(clients, "get_github_token", lambda: None)
    assert clients.build_github_client(123) is None


def test_build_github_client_returns_authenticated_client(monkeypatch):
    monkeypatch.setattr(clients, "get_github_token", lambda: "tok")
    monkeypatch.setattr(
        clients, "create_github_client", MagicMock(return_value="GH_CLIENT")
    )

    result = clients.build_github_client(123)
    assert result == "GH_CLIENT"
