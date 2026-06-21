import logging

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(f"{settings.service_name}.github")

# GitHub installation tokens are valid for one hour. Cache slightly below that
# (55 minutes) so a cached token is always refreshed before it can expire.
INSTALLATION_TOKEN_TTL_SECONDS = 55 * 60

_KEY_PREFIX = "github:installation_token"

_redis_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Return a lazily-initialized async Redis client (one per process)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
    return _redis_client


def _cache_key(installation_id: int) -> str:
    return f"{_KEY_PREFIX}:{installation_id}"


async def get_cached_installation_token(installation_id: int) -> str | None:
    """Return a cached installation token, or ``None`` on a cache miss."""
    client = get_redis_client()
    token = await client.get(_cache_key(installation_id))
    if token:
        logger.info(
            "Installation token cache hit",
            extra={"installation_id": installation_id},
        )
    else:
        logger.info(
            "Installation token cache miss",
            extra={"installation_id": installation_id},
        )
    return token


async def set_cached_installation_token(
    installation_id: int, token: str
) -> None:
    """Cache an installation token with a TTL below its true expiry."""
    client = get_redis_client()
    await client.set(
        _cache_key(installation_id),
        token,
        ex=INSTALLATION_TOKEN_TTL_SECONDS,
    )
    logger.info(
        "Installation token cached",
        extra={
            "installation_id": installation_id,
            "ttl_seconds": INSTALLATION_TOKEN_TTL_SECONDS,
        },
    )
