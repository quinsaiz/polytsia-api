import asyncio
import logging

import httpx

from src.celery_app import celery_app
from src.config import settings
from src.recommendations.constants import (
    CANDIDATE_POOL_SIZE,
    CANDIDATES_CACHE_TTL,
    GAME_CANDIDATES_CACHE_KEY,
    MIN_RATINGS_COUNT_RAWG,
    MIN_VOTE_COUNT_TMDB,
    MOVIE_CANDIDATES_CACHE_KEY,
)
from src.redis import cache_set, redis_pool

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": f"{settings.app_name}/1.0",
}


async def _fetch_movie_candidates() -> list[dict[str, object]]:
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {settings.tmdb_read_access_token}",
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0), headers=headers
    ) as client:
        response = await client.get(
            f"{settings.tmdb_base_url}/discover/movie",
            params={
                "sort_by": "vote_average.desc",
                "vote_count.gte": MIN_VOTE_COUNT_TMDB,
                "page": 1,
            },
        )
        response.raise_for_status()

    results = response.json().get("results", [])[:CANDIDATE_POOL_SIZE]
    return [
        {
            "tmdb_id": m["id"],
            "title": m["title"],
            "rating": m["vote_average"],
            "overview": m.get("overview", ""),
            "genre_ids": m.get("genre_ids", []),
            "poster_path": m.get("poster_path"),
        }
        for m in results
    ]


async def _fetch_game_candidates() -> list[dict[str, object]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0), headers={**DEFAULT_HEADERS}
    ) as client:
        response = await client.get(
            f"{settings.rawg_base_url}/games",
            params={
                "key": settings.rawg_api_key,
                "ordering": "-rating",
                "page_size": CANDIDATE_POOL_SIZE,
            },
        )
        response.raise_for_status()

    results = response.json().get("results", [])
    filtered = [
        g for g in results if g.get("ratings_count", 0) >= MIN_RATINGS_COUNT_RAWG
    ]
    return [
        {
            "rawg_id": g["id"],
            "name": g["name"],
            "rating": g["rating"],
            "background_image": g.get("background_image"),
        }
        for g in filtered
    ]


async def _refresh_movies_pipeline() -> None:
    candidates = await _fetch_movie_candidates()
    await cache_set(MOVIE_CANDIDATES_CACHE_KEY, candidates, CANDIDATES_CACHE_TTL)
    logger.info("Refreshed movie candidates pool: %d items", len(candidates))
    await redis_pool.disconnect()


async def _refresh_games_pipeline() -> None:
    candidates = await _fetch_game_candidates()
    await cache_set(GAME_CANDIDATES_CACHE_KEY, candidates, CANDIDATES_CACHE_TTL)
    logger.info("Refreshed game candidates pool: %d items", len(candidates))
    await redis_pool.disconnect()


@celery_app.task(
    name="src.recommendations.tasks.refresh_movie_candidates",
    autoretry_for=(httpx.HTTPError,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=60,
)
def refresh_movie_candidates() -> None:
    asyncio.run(_refresh_movies_pipeline())


@celery_app.task(
    name="src.recommendations.tasks.refresh_game_candidates",
    autoretry_for=(httpx.HTTPError,),
    retry_kwargs={"max_retries": 5},
    retry_backoff=True,
    retry_backoff_max=120,
)
def refresh_game_candidates() -> None:
    asyncio.run(_refresh_games_pipeline())
