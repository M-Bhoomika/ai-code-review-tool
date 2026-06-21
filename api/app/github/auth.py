import logging
import time

import httpx
import jwt
from github import Auth, Github

from app.config import settings
from app.github import redis_cache

logger = logging.getLogger(f"{settings.service_name}.github")

# GitHub requires the App JWT to live at most 10 minutes and tolerates minor
# clock drift, so we backdate ``iat`` by 60 seconds.
_JWT_IAT_BACKDATE_SECONDS = 60
_JWT_EXPIRY_SECONDS = 600
_JWT_ALGORITHM = "RS256"

_HTTP_TIMEOUT_SECONDS = 10.0


def generate_app_jwt() -> str:
    """Generate a short-lived RS256 JWT used to authenticate as the GitHub App.

    Returns the encoded JWT string.
    """
    settings.require_github_app_credentials()

    now = int(time.time())
    payload = {
        "iat": now - _JWT_IAT_BACKDATE_SECONDS,
        "exp": now + _JWT_EXPIRY_SECONDS,
        "iss": settings.GITHUB_APP_ID,
    }

    token = jwt.encode(
        payload, settings.GITHUB_PRIVATE_KEY, algorithm=_JWT_ALGORITHM
    )
    logger.info(
        "Generated GitHub App JWT",
        extra={"app_id": settings.GITHUB_APP_ID, "exp_in_seconds": _JWT_EXPIRY_SECONDS},
    )
    return token


async def _fetch_installation_token(installation_id: int) -> tuple[str, str | None]:
    """Call GitHub's installation access token endpoint.

    Returns a ``(token, expires_at)`` tuple.
    """
    app_jwt = generate_app_jwt()
    url = (
        f"{settings.GITHUB_API_URL}"
        f"/app/installations/{installation_id}/access_tokens"
    )
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()

    return data["token"], data.get("expires_at")


async def get_installation_token(installation_id: int) -> str:
    """Return a valid installation token, using Redis as a write-through cache.

    Checks the cache first; on a miss it mints a fresh token from GitHub,
    caches it, and returns it.
    """
    cached = await redis_cache.get_cached_installation_token(installation_id)
    if cached:
        return cached

    logger.info(
        "Refreshing GitHub installation token",
        extra={"installation_id": installation_id},
    )
    token, expires_at = await _fetch_installation_token(installation_id)
    await redis_cache.set_cached_installation_token(installation_id, token)
    logger.info(
        "GitHub installation token refreshed",
        extra={"installation_id": installation_id, "expires_at": expires_at},
    )
    return token


async def get_github_client(installation_id: int) -> Github:
    """Return a PyGithub client authenticated for the given installation."""
    token = await get_installation_token(installation_id)
    return Github(auth=Auth.Token(token))
