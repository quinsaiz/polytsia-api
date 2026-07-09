import logging

import httpx

from src.config import settings
from src.movies.exceptions import (
    MovieNotFoundException,
    TMDBServiceUnavailableException,
)
from src.movies.schemas import TMDBMovieSchema, TMDBSearchResultSchema
from src.redis import cache_get, cache_set

logger = logging.getLogger(__name__)

SEARCH_CACHE_TTL = 3600
MOVIE_DETAIL_CACHE_TTL = 86400


def _tmdb_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.tmdb_read_access_token}"}


async def search_movies(
    query: str,
    page: int,
    http_client: httpx.AsyncClient,
) -> TMDBSearchResultSchema:
    cache_key = f"tmdb:search:{query.lower()}:{page}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for search: %s", cache_key)
        return TMDBSearchResultSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.tmdb_base_url}/search/movie",
            headers=_tmdb_headers(),
            params={"query": query, "page": page},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("TMDB search request failed: %s", e)
        raise TMDBServiceUnavailableException() from e

    data = response.json()
    result = TMDBSearchResultSchema.model_validate(data)

    await cache_set(cache_key, data, SEARCH_CACHE_TTL)

    return result


async def get_movie_details(
    tmdb_id: int,
    http_client: httpx.AsyncClient,
) -> TMDBMovieSchema:
    cache_key = f"tmdb:movie:{tmdb_id}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for movie: %s", cache_key)
        return TMDBMovieSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.tmdb_base_url}/movie/{tmdb_id}",
            headers=_tmdb_headers(),
        )
    except httpx.HTTPError as e:
        logger.error("TMDB movie detail request failed: %s", e)
        raise TMDBServiceUnavailableException() from e

    if response.status_code == 404:
        raise MovieNotFoundException()

    response.raise_for_status()

    data = response.json()
    result = TMDBMovieSchema.model_validate(data)

    await cache_set(cache_key, data, MOVIE_DETAIL_CACHE_TTL)

    return result
