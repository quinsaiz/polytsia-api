import uuid

from fastapi import APIRouter, status

from src.auth.dependencies import CurrentUserDep
from src.database import DbSessionDep
from src.dependencies import HttpClientDep
from src.games.schemas import (
    RAWGGameSchema,
    RAWGPlatformListSchema,
    RAWGSearchResultSchema,
    TrackGameSchema,
    UpdateUserGameSchema,
    UserGameResponseSchema,
)
from src.games.service import (
    delete_user_game,
    get_game_details,
    get_platforms,
    get_user_games,
    search_games,
    track_game,
    update_user_game,
)
from src.pagination import PaginatedResponse, PaginationDep

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/search", response_model=RAWGSearchResultSchema)
async def search(
    query: str,
    http_client: HttpClientDep,
    page: int = 1,
    page_size: int = 10,
) -> RAWGSearchResultSchema:
    return await search_games(
        query=query,
        http_client=http_client,
        page=page,
        page_size=page_size,
    )


@router.get("/platforms", response_model=RAWGPlatformListSchema)
async def list_platforms(http_client: HttpClientDep) -> RAWGPlatformListSchema:
    return await get_platforms(http_client=http_client)


@router.get("/{rawg_id}", response_model=RAWGGameSchema)
async def get_details(rawg_id: int, http_client: HttpClientDep) -> RAWGGameSchema:
    return await get_game_details(rawg_id=rawg_id, http_client=http_client)


@router.post(
    "/track",
    response_model=UserGameResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def track(
    data: TrackGameSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> UserGameResponseSchema:
    user_game = await track_game(data=data, user_id=current_user.id, db=db)
    return UserGameResponseSchema.model_validate(user_game)


@router.get("/", response_model=PaginatedResponse[UserGameResponseSchema])
async def list_my_games(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    pagination: PaginationDep,
) -> PaginatedResponse[UserGameResponseSchema]:
    return await get_user_games(user_id=current_user.id, db=db, pagination=pagination)


@router.patch("/{user_game_id}", response_model=UserGameResponseSchema)
async def update(
    user_game_id: uuid.UUID,
    data: UpdateUserGameSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> UserGameResponseSchema:
    user_game = await update_user_game(
        user_game_id=user_game_id,
        data=data,
        user_id=current_user.id,
        db=db,
    )
    return UserGameResponseSchema.model_validate(user_game)


@router.delete("/{user_game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    user_game_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> None:
    await delete_user_game(user_game_id=user_game_id, user_id=current_user.id, db=db)
