import logging
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.games.exceptions import (
    GameAlreadyTrackedException,
    GameNotFoundException,
    RAWGServiceUnavailableException,
)
from src.games.models import UserGame
from src.games.schemas import (
    RAWGGameSchema,
    RAWGGenreListSchema,
    RAWGPlatformListSchema,
    RAWGSearchResultSchema,
    TrackGameSchema,
    UpdateUserGameSchema,
    UserGameResponseSchema,
)
from src.pagination import PaginatedResponse, PaginationParams
from src.redis import cache_get, cache_set

logger = logging.getLogger(__name__)

SEARCH_CACHE_TTL = 3600
GAME_DETAIL_CACHE_TTL = 86400
GENRES_CACHE_TTL = 604800
PLATFORMS_CACHE_TTL = 604800


def _rawg_headers() -> dict[str, str]:
    return {"User-Agent": settings.app_name}


async def search_games(
    query: str,
    page: int,
    page_size: int,
    http_client: httpx.AsyncClient,
) -> RAWGSearchResultSchema:
    cache_key = f"rawg:search:{query.lower()}:{page}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for search: %s", cache_key)
        return RAWGSearchResultSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.rawg_base_url}/games",
            headers=_rawg_headers(),
            params={
                "key": settings.rawg_api_key,
                "search": query,
                "page": page,
                "page_size": page_size,
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("RAWG search request failed: %s", e)
        raise RAWGServiceUnavailableException() from e

    data = response.json()
    result = RAWGSearchResultSchema.model_validate(data)

    await cache_set(cache_key, data, SEARCH_CACHE_TTL)

    return result


async def get_game_details(
    rawg_id: int,
    http_client: httpx.AsyncClient,
) -> RAWGGameSchema:
    cache_key = f"rawg:game:{rawg_id}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for game: %s", cache_key)
        return RAWGGameSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.rawg_base_url}/games/{rawg_id}",
            headers=_rawg_headers(),
            params={"key": settings.rawg_api_key},
        )
    except httpx.HTTPError as e:
        logger.error("RAWG game detail failed: %s", e)
        raise RAWGServiceUnavailableException() from e

    if response.status_code == 404:
        raise GameNotFoundException()

    response.raise_for_status()

    data = response.json()
    result = RAWGGameSchema.model_validate(data)

    await cache_set(cache_key, data, GAME_DETAIL_CACHE_TTL)

    return result


async def get_genres(http_client: httpx.AsyncClient) -> RAWGGenreListSchema:
    cache_key = "rawg:genres"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for genres")
        return RAWGGenreListSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.rawg_base_url}/genres",
            headers=_rawg_headers(),
            params={"key": settings.rawg_api_key},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("RAWG genre list request failed: %s", e)
        raise RAWGServiceUnavailableException() from e

    data = response.json()
    result = RAWGGenreListSchema.model_validate(data)

    await cache_set(cache_key, data, GENRES_CACHE_TTL)

    return result


async def get_platforms(http_client: httpx.AsyncClient) -> RAWGPlatformListSchema:
    cache_key = "rawg:platforms"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for platforms")
        return RAWGPlatformListSchema.model_validate(cached)

    try:
        response = await http_client.get(
            f"{settings.rawg_base_url}/platforms",
            headers=_rawg_headers(),
            params={"key": settings.rawg_api_key},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("RAWG platform list request failed: %s", e)
        raise RAWGServiceUnavailableException() from e

    data = response.json()
    result = RAWGPlatformListSchema.model_validate(data)

    await cache_set(cache_key, data, PLATFORMS_CACHE_TTL)

    return result


async def track_game(
    user_id: uuid.UUID,
    data: TrackGameSchema,
    db: AsyncSession,
) -> UserGame:
    stmt = select(UserGame).where(
        UserGame.user_id == user_id, UserGame.rawg_id == data.rawg_id
    )
    result = await db.execute(stmt)

    if result.scalar_one_or_none() is not None:
        raise GameAlreadyTrackedException()

    user_game = UserGame(
        user_id=user_id,
        rawg_id=data.rawg_id,
        status=data.status,
    )
    db.add(user_game)
    await db.commit()
    await db.refresh(user_game)

    logger.info("User %s tracked game rawg_id=%s", user_id, data.rawg_id)

    return user_game


async def get_user_games(
    user_id: uuid.UUID,
    pagination: PaginationParams,
    db: AsyncSession,
) -> PaginatedResponse[UserGameResponseSchema]:
    count_stmt = (
        select(func.count()).select_from(UserGame).where(UserGame.user_id == user_id)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(UserGame)
        .where(UserGame.user_id == user_id)
        .order_by(UserGame.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(stmt)
    items = [UserGameResponseSchema.model_validate(m) for m in result.scalars().all()]

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


async def get_user_game_or_404(
    user_id: uuid.UUID,
    user_game_id: uuid.UUID,
    db: AsyncSession,
) -> UserGame:
    stmt = select(UserGame).where(
        UserGame.id == user_game_id, UserGame.user_id == user_id
    )
    result = await db.execute(stmt)
    user_game = result.scalar_one_or_none()

    if user_game is None:
        raise GameNotFoundException()

    return user_game


async def update_user_game(
    user_id: uuid.UUID,
    user_game_id: uuid.UUID,
    data: UpdateUserGameSchema,
    db: AsyncSession,
) -> UserGame:
    user_game = await get_user_game_or_404(user_id, user_game_id, db)

    if data.status is not None:
        user_game.status = data.status
    if data.personal_rating is not None:
        user_game.personal_rating = data.personal_rating
    if data.tier is not None:
        user_game.tier = data.tier
    if data.notes is not None:
        user_game.notes = data.notes

    db.add(user_game)
    await db.commit()
    await db.refresh(user_game)

    logger.info("User %s updated tracked game: %s", user_id, user_game_id)

    return user_game


async def delete_user_game(
    user_id: uuid.UUID,
    user_game_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    user_game = await get_user_game_or_404(user_id, user_game_id, db)

    await db.delete(user_game)
    await db.commit()

    logger.info("User %s deleted tracked game: %s", user_id, user_game_id)
