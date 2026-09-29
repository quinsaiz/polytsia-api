import logging
import uuid
from typing import Any, cast

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.games.models import UserGame
from src.movies.models import UserMovie
from src.recommendations.constants import (
    GAME_CANDIDATES_CACHE_KEY,
    MOVIE_CANDIDATES_CACHE_KEY,
    RECOMMENDATION_MARGIN,
    RECOMMENDATIONS_LIMIT,
    TIER_WEIGHTS,
)
from src.recommendations.schemas import (
    RecommendationsResponseSchema,
    RecommendedGameSchema,
    RecommendedMovieSchema,
)
from src.redis import cache_get

logger = logging.getLogger(__name__)


def _weight_case(tier_column: Any) -> ColumnElement[float]:
    return case(
        *[(tier_column == tier, weight) for tier, weight in TIER_WEIGHTS.items()],
        else_=0.0,
    )


async def _get_weighted_threshold(
    model: type[UserMovie] | type[UserGame],
    user_id: uuid.UUID,
    db: AsyncSession,
    min_rated_count: int = 3,
) -> float | None:
    weight = _weight_case(model.tier)
    rating = model.personal_rating / (2 if model is UserGame else 1)
    stmt = select(
        func.sum(rating * weight) / func.nullif(func.sum(weight), 0),
        func.count(),
    ).where(
        model.user_id == user_id,
        model.tier.is_not(None),
        model.personal_rating.is_not(None),
    )
    result = await db.execute(stmt)
    threshold, rated_count = result.one()

    if rated_count < min_rated_count:
        return None

    return cast("float", threshold)


async def _get_tracked_tmdb_ids(user_id: uuid.UUID, db: AsyncSession) -> set[int]:
    stmt = select(UserMovie.tmdb_id).where(UserMovie.user_id == user_id)
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _get_tracked_rawg_ids(user_id: uuid.UUID, db: AsyncSession) -> set[int]:
    stmt = select(UserGame.rawg_id).where(UserGame.user_id == user_id)
    result = await db.execute(stmt)
    return set(result.scalars().all())


async def _get_recommendations[T: (RecommendedMovieSchema, RecommendedGameSchema)](
    cache_key: str,
    schema: type[T],
    id_field: str,
    threshold: float | None,
    tracked_ids: set[int],
) -> tuple[list[T], bool]:
    cached_candidates: list[dict[str, Any]] | None = await cache_get(cache_key)
    if cached_candidates is None:
        return [], False

    filtered = [
        schema.model_validate(c)
        for c in cached_candidates
        if c[id_field] not in tracked_ids
        and (threshold is None or c["rating"] >= threshold - RECOMMENDATION_MARGIN)
    ]
    filtered.sort(key=lambda item: item.rating, reverse=True)
    return filtered[:RECOMMENDATIONS_LIMIT], True


async def get_movie_recommendations(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[float | None, list[RecommendedMovieSchema], bool]:
    threshold = await _get_weighted_threshold(UserMovie, user_id, db)
    tracked_ids = await _get_tracked_tmdb_ids(user_id, db)
    movies, pool_available = await _get_recommendations(
        MOVIE_CANDIDATES_CACHE_KEY,
        RecommendedMovieSchema,
        "tmdb_id",
        threshold,
        tracked_ids,
    )
    return threshold, movies, pool_available


async def get_game_recommendations(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[float | None, list[RecommendedGameSchema], bool]:
    threshold = await _get_weighted_threshold(UserGame, user_id, db)
    tracked_ids = await _get_tracked_rawg_ids(user_id, db)
    games, pool_available = await _get_recommendations(
        GAME_CANDIDATES_CACHE_KEY,
        RecommendedGameSchema,
        "rawg_id",
        threshold,
        tracked_ids,
    )
    return threshold, games, pool_available


async def get_user_recommendations(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> RecommendationsResponseSchema:
    movie_threshold, movies, movies_pool_available = await get_movie_recommendations(
        user_id, db
    )
    game_threshold, games, games_pool_available = await get_game_recommendations(
        user_id, db
    )

    logger.info(
        "Recommendations computed for user %s: movie_threshold=%s, game_threshold=%s",
        user_id,
        movie_threshold,
        game_threshold,
    )

    return RecommendationsResponseSchema(
        movie_threshold=movie_threshold,
        game_threshold=game_threshold,
        is_movies_personalized=movie_threshold is not None,
        is_games_personalized=game_threshold is not None,
        movies_pool_available=movies_pool_available,
        games_pool_available=games_pool_available,
        movies=movies,
        games=games,
    )
