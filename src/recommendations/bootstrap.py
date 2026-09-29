import asyncio
import logging

from src.recommendations.constants import (
    GAME_CANDIDATES_CACHE_KEY,
    MOVIE_CANDIDATES_CACHE_KEY,
)
from src.recommendations.tasks import (
    refresh_game_candidates,
    refresh_movie_candidates,
)
from src.redis import get_redis_client, redis_pool

logger = logging.getLogger(__name__)


async def enqueue_missing_candidate_pools() -> list[str]:
    redis = get_redis_client()
    try:
        movie_exists = await redis.exists(MOVIE_CANDIDATES_CACHE_KEY)
        game_exists = await redis.exists(GAME_CANDIDATES_CACHE_KEY)
    finally:
        await redis_pool.disconnect()

    enqueued: list[str] = []
    if not movie_exists:
        refresh_movie_candidates.delay()
        enqueued.append("movies")
    if not game_exists:
        refresh_game_candidates.delay()
        enqueued.append("games")
    logger.info("Candidate pools queued for initial refresh: %s", enqueued)
    return enqueued


if __name__ == "__main__":
    asyncio.run(enqueue_missing_candidate_pools())
