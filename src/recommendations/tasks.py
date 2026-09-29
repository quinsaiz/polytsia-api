import asyncio
import logging

import httpx
from redis.exceptions import RedisError

from src.celery_app import celery_app
from src.config import settings
from src.recommendations.constants import (
    CANDIDATE_POOL_SIZE,
    CANDIDATES_CACHE_TTL,
    GAME_CANDIDATES_CACHE_KEY,
    MAX_CANDIDATE_PAGES,
    MIN_RATINGS_COUNT_RAWG,
    MIN_VOTE_COUNT_TMDB,
    MOVIE_CANDIDATES_CACHE_KEY,
)
from src.redis import cache_set_required, redis_pool

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_HEADERS = {
    "User-Agent": f"{settings.app_name}/1.0",
}


async def _fetch_movie_candidates() -> list[dict[str, object]]:
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {settings.tmdb_read_access_token}",
    }
    candidates: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0), headers=headers
    ) as client:
        for page in range(1, MAX_CANDIDATE_PAGES + 1):
            response = await client.get(
                f"{settings.tmdb_base_url}/discover/movie",
                params={
                    "sort_by": "vote_average.desc",
                    "vote_count.gte": MIN_VOTE_COUNT_TMDB,
                    "page": page,
                },
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", [])
            if not results:
                break

            new_ids = False
            for movie in results:
                movie_id = movie["id"]
                if movie_id in seen_ids:
                    continue
                seen_ids.add(movie_id)
                new_ids = True
                candidates.append(
                    {
                        "tmdb_id": movie_id,
                        "title": movie["title"],
                        "rating": movie["vote_average"],
                        "overview": movie.get("overview", ""),
                        "genre_ids": movie.get("genre_ids", []),
                        "poster_path": movie.get("poster_path"),
                    }
                )
                if len(candidates) == CANDIDATE_POOL_SIZE:
                    return candidates

            if not new_ids or page >= payload.get("total_pages", page):
                break

    return candidates


async def _fetch_game_candidates() -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0), headers={**DEFAULT_HEADERS}
    ) as client:
        for page in range(1, MAX_CANDIDATE_PAGES + 1):
            try:
                response = await client.get(
                    f"{settings.rawg_base_url}/games",
                    params={
                        "key": settings.rawg_api_key,
                        "ordering": "-rating",
                        "page_size": CANDIDATE_POOL_SIZE,
                        "page": page,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                reason = (
                    f"HTTP {error.response.status_code}"
                    if isinstance(error, httpx.HTTPStatusError)
                    else type(error).__name__
                )
                raise httpx.HTTPError(
                    f"RAWG candidate request failed: {reason}"
                ) from None
            payload = response.json()
            results = payload.get("results", [])
            if not results:
                break

            new_ids = False
            for game in results:
                game_id = game["id"]
                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)
                new_ids = True
                if game.get("ratings_count", 0) < MIN_RATINGS_COUNT_RAWG:
                    continue
                candidates.append(
                    {
                        "rawg_id": game_id,
                        "name": game["name"],
                        "rating": game["rating"],
                        "background_image": game.get("background_image"),
                    }
                )
                if len(candidates) == CANDIDATE_POOL_SIZE:
                    return candidates

            if not new_ids or not payload.get("next"):
                break

    return candidates


async def _refresh_movies_pipeline() -> None:
    try:
        candidates = await _fetch_movie_candidates()
        await cache_set_required(
            MOVIE_CANDIDATES_CACHE_KEY, candidates, CANDIDATES_CACHE_TTL
        )
        logger.info("Refreshed movie candidates pool: %d items", len(candidates))
    finally:
        await redis_pool.disconnect()


async def _refresh_games_pipeline() -> None:
    try:
        candidates = await _fetch_game_candidates()
        await cache_set_required(
            GAME_CANDIDATES_CACHE_KEY, candidates, CANDIDATES_CACHE_TTL
        )
        logger.info("Refreshed game candidates pool: %d items", len(candidates))
    finally:
        await redis_pool.disconnect()


@celery_app.task(
    name="src.recommendations.tasks.refresh_movie_candidates",
    autoretry_for=(httpx.HTTPError, RedisError),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_backoff_max=60,
)
def refresh_movie_candidates() -> None:
    asyncio.run(_refresh_movies_pipeline())


@celery_app.task(
    name="src.recommendations.tasks.refresh_game_candidates",
    autoretry_for=(httpx.HTTPError, RedisError),
    retry_kwargs={"max_retries": 5},
    retry_backoff=True,
    retry_backoff_max=120,
)
def refresh_game_candidates() -> None:
    asyncio.run(_refresh_games_pipeline())
