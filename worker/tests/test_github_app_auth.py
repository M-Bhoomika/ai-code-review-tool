"""Tests for worker GitHub App installation-token authentication.

No network calls: the token-exchange HTTP request is mocked, and JWTs are signed
with an in-test RSA keypair.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import clients
from app.github import github_app_auth


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Clear the token cache and App-related env before each test."""
    github_app_auth.clear_token_cache()
    for name in (
        "USE_GITHUB_APP_AUTH",
        "GITHUB_APP_ID",
        "GITHUB_PRIVATE_KEY",
        "GITHUB_API_URL",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    github_app_auth.clear_token_cache()


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"status {self.status_code}")


def _post_factory(responses, calls):
    def _post(url, headers=None, timeout=None):
        idx = len(calls)
        calls.append({"url": url, "headers": headers or {}})
        return responses[idx] if idx < len(responses) else responses[-1]

    return _post


def _future_iso(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# --- JWT generation ------------------------------------------------------------


def test_generate_app_jwt_signs_valid_rs256(monkeypatch, rsa_keys):
    private_pem, public_pem = rsa_keys
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", private_pem)

    token = github_app_auth.generate_app_jwt()
    decoded = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )

    assert decoded["iss"] == "12345"
    assert decoded["iat"] < decoded["exp"]
    # exp is at most ~10 minutes after iat (plus the iat back-date).
    assert decoded["exp"] - decoded["iat"] <= 600 + 60 + 5


def test_generate_app_jwt_requires_config(monkeypatch):
    with pytest.raises(RuntimeError):
        github_app_auth.generate_app_jwt()


def test_private_key_with_escaped_newlines(monkeypatch, rsa_keys):
    private_pem, public_pem = rsa_keys
    escaped = private_pem.replace("\n", "\\n")
    monkeypatch.setenv("GITHUB_APP_ID", "777")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", escaped)

    token = github_app_auth.generate_app_jwt()
    decoded = jwt.decode(
        token, public_pem, algorithms=["RS256"], options={"verify_aud": False}
    )
    assert decoded["iss"] == "777"


# --- Installation token exchange ----------------------------------------------


def test_installation_token_exchange(monkeypatch):
    monkeypatch.setattr(github_app_auth, "generate_app_jwt", lambda: "fake-jwt")
    calls = []
    resp = FakeResponse({"token": "ghs_abc", "expires_at": _future_iso()})
    monkeypatch.setattr(
        github_app_auth.requests, "post", _post_factory([resp], calls)
    )

    token = github_app_auth.get_installation_token(42)

    assert token == "ghs_abc"
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/app/installations/42/access_tokens")
    assert calls[0]["headers"]["Authorization"] == "Bearer fake-jwt"


def test_installation_token_uses_enterprise_base_url(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
    monkeypatch.setattr(github_app_auth, "generate_app_jwt", lambda: "jwt")
    calls = []
    resp = FakeResponse({"token": "t", "expires_at": _future_iso()})
    monkeypatch.setattr(
        github_app_auth.requests, "post", _post_factory([resp], calls)
    )

    github_app_auth.get_installation_token(3)

    assert calls[0]["url"] == (
        "https://ghe.example.com/api/v3/app/installations/3/access_tokens"
    )


# --- Caching & refresh ---------------------------------------------------------


def test_token_is_cached_until_expiry(monkeypatch):
    monkeypatch.setattr(github_app_auth, "generate_app_jwt", lambda: "jwt")
    calls = []
    resp = FakeResponse({"token": "t1", "expires_at": _future_iso()})
    monkeypatch.setattr(
        github_app_auth.requests, "post", _post_factory([resp, resp], calls)
    )

    first = github_app_auth.get_installation_token(7)
    second = github_app_auth.get_installation_token(7)

    assert first == second == "t1"
    assert len(calls) == 1  # second call served from cache


def test_token_refreshes_when_near_expiry(monkeypatch):
    monkeypatch.setattr(github_app_auth, "generate_app_jwt", lambda: "jwt")
    calls = []
    expired = FakeResponse({"token": "old", "expires_at": _past_iso()})
    fresh = FakeResponse({"token": "new", "expires_at": _future_iso()})
    monkeypatch.setattr(
        github_app_auth.requests, "post", _post_factory([expired, fresh], calls)
    )

    first = github_app_auth.get_installation_token(9)
    second = github_app_auth.get_installation_token(9)

    assert first == "old"
    assert second == "new"
    assert len(calls) == 2  # stale token forced a refresh


def test_separate_installations_cached_independently(monkeypatch):
    monkeypatch.setattr(github_app_auth, "generate_app_jwt", lambda: "jwt")
    calls = []
    r1 = FakeResponse({"token": "a", "expires_at": _future_iso()})
    r2 = FakeResponse({"token": "b", "expires_at": _future_iso()})
    monkeypatch.setattr(
        github_app_auth.requests, "post", _post_factory([r1, r2], calls)
    )

    assert github_app_auth.get_installation_token(1) == "a"
    assert github_app_auth.get_installation_token(2) == "b"
    assert len(calls) == 2


def test_build_github_client_uses_installation_token(monkeypatch):
    monkeypatch.setattr(
        github_app_auth, "get_installation_token", lambda inst: "tok-123"
    )
    captured = {}

    def fake_github(auth=None, **kwargs):
        captured["auth"] = auth
        return MagicMock(name="github")

    monkeypatch.setattr(github_app_auth, "Github", fake_github)

    client = github_app_auth.build_github_client(5)

    assert client is not None
    assert captured["auth"] is not None


# --- Fallback PAT path ---------------------------------------------------------


def test_pat_path_when_flag_disabled(monkeypatch):
    # USE_GITHUB_APP_AUTH unset -> PAT path.
    monkeypatch.setattr(clients, "get_github_token", lambda: "tok")
    monkeypatch.setattr(clients, "create_github_client", lambda: "PAT_CLIENT")
    app_builder = MagicMock()
    monkeypatch.setattr(
        clients.github_app_auth, "build_github_client", app_builder
    )

    result = clients.build_github_client(123)

    assert result == "PAT_CLIENT"
    app_builder.assert_not_called()


def test_app_path_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("USE_GITHUB_APP_AUTH", "true")
    monkeypatch.setattr(
        clients.github_app_auth,
        "build_github_client",
        lambda inst: "APP_CLIENT",
    )
    pat_builder = MagicMock()
    monkeypatch.setattr(clients, "create_github_client", pat_builder)

    result = clients.build_github_client(55)

    assert result == "APP_CLIENT"
    pat_builder.assert_not_called()


def test_app_path_failure_returns_none(monkeypatch):
    monkeypatch.setenv("USE_GITHUB_APP_AUTH", "true")

    def boom(inst):
        raise RuntimeError("missing key")

    monkeypatch.setattr(clients.github_app_auth, "build_github_client", boom)

    assert clients.build_github_client(1) is None


def test_pat_path_returns_none_without_token(monkeypatch):
    # Flag disabled and no PAT configured.
    monkeypatch.setattr(clients, "get_github_token", lambda: None)

    assert clients.build_github_client(1) is None
