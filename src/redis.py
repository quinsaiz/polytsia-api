import json
import logging
from typing import Any

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from src.config import settings

logger = logging.getLogger(__name__)

redis_pool = ConnectionPool.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> Redis:
    return Redis(connection_pool=redis_pool)


async def cache_get(key: str) -> Any | None:
    try:
        redis = get_redis_client()
        value = await redis.get(key)
        if value is None:
            return None
        return json.loads(value)
    except Exception as e:
        logger.warning("Cache get failed for key '%s': %s", key, e)
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        redis = get_redis_client()
        await redis.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as e:
        logger.warning("Cache set failed for key '%s': %s", key, e)


async def cache_set_required(key: str, value: Any, ttl_seconds: int) -> None:
    redis = get_redis_client()
    written = await redis.set(key, json.dumps(value), ex=ttl_seconds)
    if not written:
        raise RedisError(f"Cache set failed for key '{key}'")


async def cache_delete(key: str) -> None:
    try:
        redis = get_redis_client()
        await redis.delete(key)
    except Exception as e:
        logger.warning("Cache delete failed for key '%s': %s", key, e)
