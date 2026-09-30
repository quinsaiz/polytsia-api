import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from src.auth.dependencies import CurrentUserDep
from src.database import DbSessionDep
from src.dependencies import HttpClientDep
from src.movies.schemas import (
    TMDBGenreListSchema,
    TMDBMovieSchema,
    TMDBSearchResultSchema,
    TrackMovieSchema,
    UpdateUserMovieSchema,
    UserMovieResponseSchema,
)
from src.movies.service import (
    delete_user_movie,
    get_genres,
    get_movie_details,
    get_user_movie_by_catalog_or_404,
    get_user_movie_or_404,
    get_user_movies,
    search_movies,
    track_movie,
    update_user_movie,
)
from src.pagination import PaginatedResponse, PaginationDep

router = APIRouter(prefix="/movies", tags=["movies"])


@router.get("/search", response_model=TMDBSearchResultSchema)
async def search(
    query: str,
    http_client: HttpClientDep,
    page: Annotated[int, Query(ge=1, le=500)] = 1,
) -> TMDBSearchResultSchema:
    return await search_movies(query=query, http_client=http_client, page=page)


@router.get("/genres", response_model=TMDBGenreListSchema)
async def list_genres(http_client: HttpClientDep) -> TMDBGenreListSchema:
    return await get_genres(http_client=http_client)


@router.get("/tracked/by-catalog/{tmdb_id}", response_model=UserMovieResponseSchema)
async def get_my_movie_by_catalog(
    tmdb_id: int,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> UserMovieResponseSchema:
    record = await get_user_movie_by_catalog_or_404(current_user.id, tmdb_id, db)
    return UserMovieResponseSchema.model_validate(record)


@router.get("/tracked/{user_movie_id}", response_model=UserMovieResponseSchema)
async def get_my_movie(
    user_movie_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> UserMovieResponseSchema:
    record = await get_user_movie_or_404(current_user.id, user_movie_id, db)
    return UserMovieResponseSchema.model_validate(record)


@router.get("/{tmdb_id}", response_model=TMDBMovieSchema)
async def get_details(tmdb_id: int, http_client: HttpClientDep) -> TMDBMovieSchema:
    return await get_movie_details(tmdb_id=tmdb_id, http_client=http_client)


@router.post(
    "/track",
    response_model=UserMovieResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def track(
    data: TrackMovieSchema,
    current_user: CurrentUserDep,
    http_client: HttpClientDep,
    db: DbSessionDep,
) -> UserMovieResponseSchema:
    user_movie = await track_movie(
        data=data,
        user_id=current_user.id,
        http_client=http_client,
        db=db,
    )
    return UserMovieResponseSchema.model_validate(user_movie)


@router.get("/", response_model=PaginatedResponse[UserMovieResponseSchema])
async def list_my_movies(
    current_user: CurrentUserDep,
    db: DbSessionDep,
    pagination: PaginationDep,
) -> PaginatedResponse[UserMovieResponseSchema]:
    return await get_user_movies(user_id=current_user.id, db=db, pagination=pagination)


@router.patch("/{user_movie_id}", response_model=UserMovieResponseSchema)
async def update(
    user_movie_id: uuid.UUID,
    data: UpdateUserMovieSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> UserMovieResponseSchema:
    user_movie = await update_user_movie(
        user_movie_id=user_movie_id,
        data=data,
        user_id=current_user.id,
        db=db,
    )
    return UserMovieResponseSchema.model_validate(user_movie)


@router.delete("/{user_movie_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    user_movie_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> None:
    await delete_user_movie(user_movie_id=user_movie_id, user_id=current_user.id, db=db)
