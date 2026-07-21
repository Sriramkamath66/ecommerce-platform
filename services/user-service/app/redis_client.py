from __future__ import annotations

import logging

from redis.asyncio import Redis, ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level pool; initialised during app startup via init_redis().
redis_pool: Redis | None = None


async def init_redis() -> Redis:
    """Create the async Redis connection pool and store it module-globally."""
    global redis_pool
    pool = ConnectionPool.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        max_connections=20,
    )
    redis_pool = Redis(connection_pool=pool)
    # Smoke-test the connection
    await redis_pool.ping()
    logger.info("Redis connection pool ready: %s", settings.REDIS_URL)
    return redis_pool


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global redis_pool
    if redis_pool is not None:
        await redis_pool.aclose()
        redis_pool = None
        logger.info("Redis connection pool closed.")


def get_redis_client() -> Redis | None:
    """Return the current Redis client (may be None before startup)."""
    return redis_pool
