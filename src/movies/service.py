import logging
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.catalog_snapshot import catalog_snapshot
from src.config import settings
from src.database import is_unique_constraint_violation
from src.movies.constants import (
    GENRES_CACHE_TTL,
    MOVIE_DETAIL_CACHE_TTL,
    SEARCH_CACHE_TTL,
)
from src.movies.exceptions import (
    MovieAlreadyTrackedException,
    MovieNotFoundException,
    TMDBServiceUnavailableException,
)
from src.movies.models import UserMovie
from src.movies.schemas import (
    TMDBGenreListSchema,
    TMDBMovieSchema,
    TMDBSearchResultSchema,
    TrackMovieSchema,
    UpdateUserMovieSchema,
    UserMovieResponseSchema,
)
from src.pagination import PaginatedResponse, PaginationParams
from src.redis import cache_get, cache_set
from src.upstream import InvalidUpstreamPayload, parse_upstream

logger = logging.getLogger(__name__)


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
        result = parse_upstream(response, TMDBSearchResultSchema)
    except InvalidUpstreamPayload:
        raise TMDBServiceUnavailableException() from None
    except httpx.HTTPError as e:
        logger.error("TMDB search request failed: %s", e)
        raise TMDBServiceUnavailableException() from e

    await cache_set(cache_key, result.model_dump(mode="json"), SEARCH_CACHE_TTL)

    return result


async def get_movie_details(
    tmdb_id: int,
    http_client: httpx.AsyncClient,
    *,
    use_cache: bool = True,
) -> TMDBMovieSchema:
    cache_key = f"tmdb:movie:{tmdb_id}"

    cached = await cache_get(cache_key) if use_cache else None
    if cached is not None:
        logger.debug("Cache hit for movie: %s", cache_key)
        return TMDBMovieSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.tmdb_base_url}/movie/{tmdb_id}",
            headers=_tmdb_headers(),
        )
        if response.status_code == 404:
            raise MovieNotFoundException()
        response.raise_for_status()
        result = parse_upstream(response, TMDBMovieSchema)
    except InvalidUpstreamPayload:
        raise TMDBServiceUnavailableException() from None
    except httpx.HTTPError as e:
        status = (
            e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        )
        logger.error(
            "TMDB movie detail request failed: %s, status=%s", type(e).__name__, status
        )
        raise TMDBServiceUnavailableException() from None

    if use_cache:
        await cache_set(
            cache_key, result.model_dump(mode="json"), MOVIE_DETAIL_CACHE_TTL
        )

    return result


async def get_genres(http_client: httpx.AsyncClient) -> TMDBGenreListSchema:
    cache_key = "tmdb:genres"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for genres")
        return TMDBGenreListSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.tmdb_base_url}/genre/movie/list",
            headers=_tmdb_headers(),
        )
        response.raise_for_status()
        result = parse_upstream(response, TMDBGenreListSchema)
    except InvalidUpstreamPayload:
        raise TMDBServiceUnavailableException() from None
    except httpx.HTTPError as e:
        logger.error("TMDB genres request failed: %s", e)
        raise TMDBServiceUnavailableException() from e

    await cache_set(cache_key, result.model_dump(mode="json"), GENRES_CACHE_TTL)

    return result


async def track_movie(
    user_id: uuid.UUID,
    data: TrackMovieSchema,
    http_client: httpx.AsyncClient,
    db: AsyncSession,
) -> UserMovie:
    stmt = select(UserMovie).where(
        UserMovie.user_id == user_id,
        UserMovie.tmdb_id == data.tmdb_id,
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise MovieAlreadyTrackedException()

    movie_details = await get_movie_details(
        tmdb_id=data.tmdb_id, http_client=http_client
    )

    user_movie = UserMovie(
        user_id=user_id,
        tmdb_id=data.tmdb_id,
        status=data.status,
        **catalog_snapshot(movie_details),
        external_rating=movie_details.vote_average,
    )
    db.add(user_movie)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if is_unique_constraint_violation(error, "uq_user_movie"):
            raise MovieAlreadyTrackedException() from error
        raise
    await db.refresh(user_movie)

    logger.info("User %s tracked movie tmdb_id=%s", user_id, data.tmdb_id)

    return user_movie


async def get_user_movies(
    user_id: uuid.UUID,
    pagination: PaginationParams,
    db: AsyncSession,
) -> PaginatedResponse[UserMovieResponseSchema]:
    count_stmt = (
        select(func.count()).select_from(UserMovie).where(UserMovie.user_id == user_id)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(UserMovie)
        .where(UserMovie.user_id == user_id)
        .order_by(UserMovie.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(stmt)
    items = [UserMovieResponseSchema.model_validate(m) for m in result.scalars().all()]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def get_user_movie_or_404(
    user_id: uuid.UUID,
    user_movie_id: uuid.UUID,
    db: AsyncSession,
) -> UserMovie:
    stmt = select(UserMovie).where(
        UserMovie.id == user_movie_id,
        UserMovie.user_id == user_id,
    )
    result = await db.execute(stmt)
    user_movie = result.scalar_one_or_none()

    if user_movie is None:
        raise MovieNotFoundException()

    return user_movie


async def get_user_movie_by_catalog_or_404(
    user_id: uuid.UUID,
    tmdb_id: int,
    db: AsyncSession,
) -> UserMovie:
    record = await db.scalar(
        select(UserMovie).where(
            UserMovie.user_id == user_id,
            UserMovie.tmdb_id == tmdb_id,
        )
    )
    if record is None:
        raise MovieNotFoundException()
    return record


async def update_user_movie(
    user_id: uuid.UUID,
    user_movie_id: uuid.UUID,
    data: UpdateUserMovieSchema,
    db: AsyncSession,
) -> UserMovie:
    user_movie = await get_user_movie_or_404(user_id, user_movie_id, db)

    if "status" in data.model_fields_set:
        user_movie.status = data.status
    if "personal_rating" in data.model_fields_set:
        user_movie.personal_rating = data.personal_rating
    if "tier" in data.model_fields_set:
        user_movie.tier = data.tier
    if "notes" in data.model_fields_set:
        user_movie.notes = data.notes

    db.add(user_movie)
    await db.commit()
    await db.refresh(user_movie)

    logger.info("User %s updated tracked movie: %s", user_id, user_movie_id)

    return user_movie


async def delete_user_movie(
    user_id: uuid.UUID,
    user_movie_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    from src.tierlists.service import delete_tracked_media_and_compact

    stmt = (
        select(UserMovie)
        .where(UserMovie.id == user_movie_id, UserMovie.user_id == user_id)
        .with_for_update()
    )
    user_movie = (await db.execute(stmt)).scalar_one_or_none()
    if user_movie is None:
        raise MovieNotFoundException()

    await delete_tracked_media_and_compact(user_movie, db)

    logger.info("User %s removed tracked movie: %s", user_id, user_movie_id)
