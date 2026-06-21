"""Worker-side GitHub App installation-token authentication.

Implements the real GitHub App auth flow so the worker can act as the installed
App rather than using a static personal access token (PAT):

1. Build a short-lived RS256 JWT signed with the App's private key
   (``iss`` = App ID, plus ``iat``/``exp``).
2. Exchange that JWT for an installation access token via the GitHub API.
3. Cache the installation token in-process until shortly before it expires.

The PAT path remains available as a fallback (see ``app.clients`` and the
``USE_GITHUB_APP_AUTH`` flag). This module only implements the App flow; the
flag-based dispatch lives at the single call site.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import jwt
import requests
from github import Auth, Github

logger = logging.getLogger("ai-code-review-worker.github_app")

GITHUB_APP_ID_ENV = "GITHUB_APP_ID"
GITHUB_PRIVATE_KEY_ENV = "GITHUB_PRIVATE_KEY"
GITHUB_API_URL_ENV = "GITHUB_API_URL"
USE_GITHUB_APP_AUTH_ENV = "USE_GITHUB_APP_AUTH"

# GitHub requires the App JWT to live at most 10 minutes; back-date ``iat`` by 60
# seconds to tolerate minor clock drift between this host and GitHub.
_JWT_IAT_BACKDATE_SECONDS = 60
_JWT_EXPIRY_SECONDS = 600
_JWT_ALGORITHM = "RS256"

_HTTP_TIMEOUT_SECONDS = 10.0
_DEFAULT_API_URL = "https://api.github.com"

# Refresh an installation token this many seconds before its real expiry so a
# returned token is always comfortably valid for the work that follows.
_TOKEN_EXPIRY_MARGIN_SECONDS = 300
# Fallback lifetime if GitHub omits ``expires_at`` (tokens last ~1 hour).
_DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass
class _CachedToken:
    token: str
    # Epoch seconds after which the token should be considered stale (already
    # adjusted by the safety margin).
    refresh_after: float


_cache_lock = threading.Lock()
_token_cache: dict[int, _CachedToken] = {}


def use_github_app_auth() -> bool:
    """Return True when GitHub App installation-token auth is enabled."""
    return (
        os.getenv(USE_GITHUB_APP_AUTH_ENV, "false").strip().lower() in _TRUE_VALUES
    )


def _api_base_url() -> str:
    return (os.getenv(GITHUB_API_URL_ENV) or _DEFAULT_API_URL).strip().rstrip("/")


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not configured; cannot use GitHub App authentication"
        )
    return value


def _normalize_private_key(raw_key: str) -> str:
    """Normalize a PEM key supplied via env (which often escapes newlines)."""
    # Env vars frequently store the PEM with literal "\n" sequences.
    return raw_key.replace("\\n", "\n").strip() + "\n"


def generate_app_jwt() -> str:
    """Generate a short-lived RS256 JWT authenticating as the GitHub App."""
    app_id = _require_env(GITHUB_APP_ID_ENV)
    private_key = _normalize_private_key(_require_env(GITHUB_PRIVATE_KEY_ENV))

    now = int(time.time())
    payload = {
        "iat": now - _JWT_IAT_BACKDATE_SECONDS,
        "exp": now + _JWT_EXPIRY_SECONDS,
        "iss": app_id,
    }
    token = jwt.encode(payload, private_key, algorithm=_JWT_ALGORITHM)
    logger.info("github_app_jwt_generated", extra={"app_id": app_id})
    return token


def _parse_expires_at(expires_at: Optional[str]) -> float:
    """Convert GitHub's ISO-8601 ``expires_at`` to an epoch timestamp."""
    if expires_at:
        try:
            normalized = expires_at.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            logger.warning(
                "github_app_expires_at_unparseable",
                extra={"expires_at": expires_at},
            )
    return time.time() + _DEFAULT_TOKEN_LIFETIME_SECONDS


def _exchange_jwt_for_installation_token(
    installation_id: int,
) -> tuple[str, Optional[str]]:
    """Call GitHub's installation access-token endpoint; return (token, expires_at)."""
    app_jwt = generate_app_jwt()
    url = f"{_api_base_url()}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.post(url, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    return data["token"], data.get("expires_at")


def get_installation_token(installation_id: int) -> str:
    """Return a valid installation token, refreshing it shortly before expiry.

    Tokens are cached in-process per installation id and reused until they near
    expiry, at which point a fresh token is minted from a new App JWT.
    """
    now = time.time()
    with _cache_lock:
        cached = _token_cache.get(installation_id)
        if cached is not None and cached.refresh_after > now:
            logger.info(
                "installation_token_cache_hit",
                extra={"installation_id": installation_id},
            )
            return cached.token

    logger.info(
        "installation_token_refreshing",
        extra={"installation_id": installation_id},
    )
    token, expires_at = _exchange_jwt_for_installation_token(installation_id)
    refresh_after = _parse_expires_at(expires_at) - _TOKEN_EXPIRY_MARGIN_SECONDS

    with _cache_lock:
        _token_cache[installation_id] = _CachedToken(
            token=token, refresh_after=refresh_after
        )
    logger.info(
        "installation_token_cached",
        extra={
            "installation_id": installation_id,
            "expires_at": expires_at,
        },
    )
    return token


def build_github_client(installation_id: int) -> Github:
    """Return a PyGithub client authenticated with an installation token."""
    token = get_installation_token(installation_id)
    auth = Auth.Token(token)
    base_url = (os.getenv(GITHUB_API_URL_ENV) or "").strip()
    if base_url:
        logger.info(
            "github_app_client_created",
            extra={"installation_id": installation_id, "base_url": base_url},
        )
        return Github(auth=auth, base_url=base_url)
    logger.info(
        "github_app_client_created",
        extra={"installation_id": installation_id, "base_url": "default"},
    )
    return Github(auth=auth)


def clear_token_cache() -> None:
    """Clear the in-process installation-token cache (intended for tests)."""
    with _cache_lock:
        _token_cache.clear()
