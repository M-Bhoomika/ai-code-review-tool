import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.github import auth, redis_cache

APP_ID = "123456"


@pytest.fixture
def rsa_keypair() -> tuple[str, str]:
    """Generate an RSA keypair and return (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture
def github_credentials(monkeypatch, rsa_keypair):
    private_pem, public_pem = rsa_keypair
    monkeypatch.setattr(settings, "GITHUB_APP_ID", APP_ID)
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", private_pem)
    return public_pem


def test_generate_app_jwt_claims(github_credentials):
    public_pem = github_credentials

    before = int(time.time())
    token = auth.generate_app_jwt()
    after = int(time.time())

    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])

    assert decoded["iss"] == APP_ID
    # iat is backdated by 60 seconds to tolerate clock drift.
    assert before - 60 <= decoded["iat"] <= after - 60 + 1
    # exp is 600 seconds after now, i.e. 660 seconds after the backdated iat.
    assert decoded["exp"] - decoded["iat"] == 660
    assert decoded["exp"] <= after + 600


def test_cache_hit_skips_github_call(monkeypatch):
    async def fake_get_cached(installation_id):
        return "cached-token"

    async def fail_fetch(installation_id):
        raise AssertionError("GitHub should not be called on a cache hit")

    monkeypatch.setattr(
        redis_cache, "get_cached_installation_token", fake_get_cached
    )
    monkeypatch.setattr(auth, "_fetch_installation_token", fail_fetch)

    token = asyncio.run(auth.get_installation_token(42))

    assert token == "cached-token"


def test_cache_miss_fetches_and_caches_token(monkeypatch):
    set_calls: list[tuple[int, str]] = []

    async def fake_get_cached(installation_id):
        return None

    async def fake_fetch(installation_id):
        return "fresh-token", "2026-01-01T00:00:00Z"

    async def fake_set_cached(installation_id, token):
        set_calls.append((installation_id, token))

    monkeypatch.setattr(
        redis_cache, "get_cached_installation_token", fake_get_cached
    )
    monkeypatch.setattr(auth, "_fetch_installation_token", fake_fetch)
    monkeypatch.setattr(
        redis_cache, "set_cached_installation_token", fake_set_cached
    )

    token = asyncio.run(auth.get_installation_token(99))

    assert token == "fresh-token"
    assert set_calls == [(99, "fresh-token")]


def test_token_cached_after_fetch_uses_http_mock(monkeypatch, github_credentials):
    """End-to-end cache miss with the GitHub HTTP call mocked."""
    set_calls: list[tuple[int, str]] = []

    async def fake_get_cached(installation_id):
        return None

    async def fake_set_cached(installation_id, token):
        set_calls.append((installation_id, token))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "token": "ghs_mocktoken",
                "expires_at": "2026-01-01T01:00:00Z",
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None):
            assert "/app/installations/7/access_tokens" in url
            assert headers["Authorization"].startswith("Bearer ")
            return FakeResponse()

    monkeypatch.setattr(
        redis_cache, "get_cached_installation_token", fake_get_cached
    )
    monkeypatch.setattr(
        redis_cache, "set_cached_installation_token", fake_set_cached
    )
    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeAsyncClient)

    token = asyncio.run(auth.get_installation_token(7))

    assert token == "ghs_mocktoken"
    assert set_calls == [(7, "ghs_mocktoken")]
