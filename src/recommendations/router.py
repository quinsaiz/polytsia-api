from fastapi import APIRouter

from src.auth.dependencies import CurrentUserDep
from src.database import DbSessionDep
from src.recommendations.schemas import (
    GameRecommendationsResponseSchema,
    MovieRecommendationsResponseSchema,
    RecommendationsResponseSchema,
)
from src.recommendations.service import (
    get_game_recommendations,
    get_movie_recommendations,
    get_user_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])


@router.get("/", response_model=RecommendationsResponseSchema)
async def get_recommendations(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> RecommendationsResponseSchema:
    return await get_user_recommendations(user_id=current_user.id, db=db)


@router.get("/movies", response_model=MovieRecommendationsResponseSchema)
async def get_movie_recs(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> MovieRecommendationsResponseSchema:
    threshold, movies = await get_movie_recommendations(user_id=current_user.id, db=db)
    return MovieRecommendationsResponseSchema(
        threshold=threshold,
        is_personalized=threshold is not None,
        movies=movies,
    )


@router.get("/games", response_model=GameRecommendationsResponseSchema)
async def get_game_recs(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> GameRecommendationsResponseSchema:
    threshold, games = await get_game_recommendations(user_id=current_user.id, db=db)
    return GameRecommendationsResponseSchema(
        threshold=threshold,
        is_personalized=threshold is not None,
        games=games,
    )
