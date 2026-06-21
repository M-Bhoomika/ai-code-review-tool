import hashlib
import hmac
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import webhooks
from app.config import settings
from app.main import app

SECRET = "test-webhook-secret"


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", SECRET)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_dispatch(monkeypatch) -> MagicMock:
    mock_delay = MagicMock()
    monkeypatch.setattr(webhooks.review_pull_request, "delay", mock_delay)
    return mock_delay


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _pr_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"number": 7},
        "installation": {"id": 123},
    }


def test_valid_signature_accepted(client, mock_dispatch):
    body = json.dumps(_pr_payload()).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    mock_dispatch.assert_called_once_with("octocat/hello-world", 7, 123)


def test_invalid_signature_rejected(client, mock_dispatch):
    body = json.dumps(_pr_payload()).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 403
    mock_dispatch.assert_not_called()


def test_missing_signature_rejected(client, mock_dispatch):
    body = json.dumps(_pr_payload()).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 403
    mock_dispatch.assert_not_called()


def test_unsupported_event_ignored(client, mock_dispatch):
    body = json.dumps({"zen": "Keep it simple."}).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    mock_dispatch.assert_not_called()


def test_unsupported_pr_action_ignored(client, mock_dispatch):
    body = json.dumps(_pr_payload(action="closed")).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    mock_dispatch.assert_not_called()


@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
def test_celery_dispatch_called_for_supported_actions(
    client, mock_dispatch, action
):
    body = json.dumps(_pr_payload(action=action)).encode("utf-8")
    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    mock_dispatch.assert_called_once_with("octocat/hello-world", 7, 123)
