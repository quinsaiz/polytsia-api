import uuid
from datetime import datetime

from pydantic import BaseModel

from src.movies.constants import TierRank, WatchStatus


class TMDBMovieSchema(BaseModel):
    id: int
    title: str
    overview: str
    release_date: str | None = None
    poster_path: str | None = None
    vote_average: float
    genre_ids: list[int] = []


class TMDBSearchResultSchema(BaseModel):
    page: int
    results: list[TMDBMovieSchema]
    total_pages: int
    total_results: int


class TrackMovieSchema(BaseModel):
    tmdb_id: int
    status: WatchStatus = WatchStatus.PLANNED


class UpdateUserMovieSchema(BaseModel):
    status: WatchStatus | None = None
    personal_rating: int | None = None
    tier: TierRank | None = None
    notes: str | None = None


class UserMovieResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    tmdb_id: int
    status: WatchStatus
    personal_rating: int | None
    tier: TierRank | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
