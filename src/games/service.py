import logging
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import is_unique_constraint_violation
from src.games.constants import (
    GAME_DETAIL_CACHE_TTL,
    GENRES_CACHE_TTL,
    PLATFORMS_CACHE_TTL,
    SEARCH_CACHE_TTL,
    SEARCH_MAX_PAGE,
)
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
from src.upstream import InvalidUpstreamPayload, parse_upstream

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _log_rawg_http_error(action: str, error: httpx.HTTPError) -> None:
    status = (
        error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    )
    logger.error("RAWG %s failed: %s, status=%s", action, type(error).__name__, status)


def _rawg_headers() -> dict[str, str]:
    return {"User-Agent": settings.app_name}


def _local_page_link(path: str, page: int, params: dict[str, str | int]) -> str:
    return f"{path}?{urlencode({**params, 'page': page})}"


def _safe_search_result(
    data: object, query: str, page: int, page_size: int
) -> RAWGSearchResultSchema:
    result = RAWGSearchResultSchema.model_validate(data)
    path = "/api/v1/games/search"
    params: dict[str, str | int] = {"query": query, "page_size": page_size}
    return result.model_copy(
        update={
            "next": (
                _local_page_link(path, page + 1, params)
                if result.next is not None and page < SEARCH_MAX_PAGE
                else None
            ),
            "previous": (
                _local_page_link(path, page - 1, params)
                if result.previous is not None and page > 1
                else None
            ),
        }
    )


def _safe_platform_list(data: object, page: int) -> RAWGPlatformListSchema:
    result = RAWGPlatformListSchema.model_validate(data)
    path = "/api/v1/games/platforms"
    params: dict[str, str | int] = {}
    return result.model_copy(
        update={
            "next": (
                _local_page_link(path, page + 1, params)
                if result.next is not None
                else None
            ),
            "previous": (
                _local_page_link(path, page - 1, params)
                if result.previous is not None and page > 1
                else None
            ),
        }
    )


async def search_games(
    query: str,
    page: int,
    page_size: int,
    http_client: httpx.AsyncClient,
) -> RAWGSearchResultSchema:
    cache_key = f"rawg:search:v2:{urlencode({'query': query, 'page': page, 'page_size': page_size})}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for search: %s", cache_key)
        return _safe_search_result(cached, query, page, page_size)

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
        result = parse_upstream(response, RAWGSearchResultSchema)
    except InvalidUpstreamPayload:
        raise RAWGServiceUnavailableException() from None
    except httpx.HTTPError as e:
        _log_rawg_http_error("search", e)
        raise RAWGServiceUnavailableException() from None

    result = _safe_search_result(result, query, page, page_size)

    await cache_set(cache_key, result.model_dump(mode="json"), SEARCH_CACHE_TTL)

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
        if response.status_code == 404:
            raise GameNotFoundException()
        response.raise_for_status()
        result = parse_upstream(response, RAWGGameSchema)
    except InvalidUpstreamPayload:
        raise RAWGServiceUnavailableException() from None
    except httpx.HTTPError as e:
        _log_rawg_http_error("game detail", e)
        raise RAWGServiceUnavailableException() from None

    await cache_set(cache_key, result.model_dump(mode="json"), GAME_DETAIL_CACHE_TTL)

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
        result = parse_upstream(response, RAWGGenreListSchema)
    except InvalidUpstreamPayload:
        raise RAWGServiceUnavailableException() from None
    except httpx.HTTPError as e:
        _log_rawg_http_error("genre list", e)
        raise RAWGServiceUnavailableException() from None

    await cache_set(cache_key, result.model_dump(mode="json"), GENRES_CACHE_TTL)

    return result


async def get_platforms(
    http_client: httpx.AsyncClient, page: int = 1
) -> RAWGPlatformListSchema:
    cache_key = f"rawg:platforms:v2:{page}"

    cached = await cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for platforms")
        return _safe_platform_list(cached, page)

    try:
        response = await http_client.get(
            f"{settings.rawg_base_url}/platforms",
            headers=_rawg_headers(),
            params={"key": settings.rawg_api_key, "page": page},
        )
        response.raise_for_status()
        result = parse_upstream(response, RAWGPlatformListSchema)
    except InvalidUpstreamPayload:
        raise RAWGServiceUnavailableException() from None
    except httpx.HTTPError as e:
        _log_rawg_http_error("platform list", e)
        raise RAWGServiceUnavailableException() from None

    result = _safe_platform_list(result, page)

    await cache_set(cache_key, result.model_dump(mode="json"), PLATFORMS_CACHE_TTL)

    return result


async def track_game(
    user_id: uuid.UUID,
    data: TrackGameSchema,
    http_client: httpx.AsyncClient,
    db: AsyncSession,
) -> UserGame:
    stmt = select(UserGame).where(
        UserGame.user_id == user_id,
        UserGame.rawg_id == data.rawg_id,
    )
    result = await db.execute(stmt)

    if result.scalar_one_or_none() is not None:
        raise GameAlreadyTrackedException()

    game_details = await get_game_details(rawg_id=data.rawg_id, http_client=http_client)

    user_game = UserGame(
        user_id=user_id,
        rawg_id=data.rawg_id,
        status=data.status,
        external_rating=game_details.rating,
    )
    db.add(user_game)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if is_unique_constraint_violation(error, "uq_user_game"):
            raise GameAlreadyTrackedException() from error
        raise
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
        UserGame.id == user_game_id,
        UserGame.user_id == user_id,
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

    if "status" in data.model_fields_set:
        user_game.status = data.status
    if "personal_rating" in data.model_fields_set:
        user_game.personal_rating = data.personal_rating
    if "tier" in data.model_fields_set:
        user_game.tier = data.tier
    if "notes" in data.model_fields_set:
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
    from src.tierlists.service import delete_tracked_media_and_compact

    stmt = (
        select(UserGame)
        .where(UserGame.id == user_game_id, UserGame.user_id == user_id)
        .with_for_update()
    )
    user_game = (await db.execute(stmt)).scalar_one_or_none()
    if user_game is None:
        raise GameNotFoundException()

    await delete_tracked_media_and_compact(user_game, db)

    logger.info("User %s deleted tracked game: %s", user_id, user_game_id)
