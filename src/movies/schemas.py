import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from src.movies.constants import TierRank, WatchStatus


class TMDBMovieSchema(BaseModel):
    id: int
    title: str
    overview: str
    release_date: str | None = None
    poster_path: str | None = None
    vote_average: float
    genre_ids: list[int] = []

    @model_validator(mode="before")
    @classmethod
    def extract_genre_ids(cls, data: dict[str, object]) -> dict[str, object]:
        """
        TMDB's /search/movie returns genre_ids: [28, 80].
        TMDB's /movie/{id} returns genres: [{"id": 28, "name": "Action"}].
        Normalize both into genre_ids so the schema works for both endpoints.
        """
        genres = data.get("genres")
        if "genre_ids" not in data and isinstance(genres, list):
            data["genre_ids"] = [
                genre["id"] for genre in genres if isinstance(genre, dict)
            ]
        return data


class TMDBGenreSchema(BaseModel):
    id: int
    name: str


class TMDBGenreListSchema(BaseModel):
    genres: list[TMDBGenreSchema]


class TMDBSearchResultSchema(BaseModel):
    page: int
    results: list[TMDBMovieSchema]
    total_pages: int
    total_results: int


class TrackMovieSchema(BaseModel):
    tmdb_id: int
    status: WatchStatus = WatchStatus.PLANNED


class UpdateUserMovieSchema(BaseModel):
    status: WatchStatus = WatchStatus.PLANNED
    personal_rating: int | None = Field(default=None, ge=0, le=10)
    tier: TierRank | None = None
    notes: str | None = Field(default=None, max_length=1000)


class UserMovieResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    tmdb_id: int
    status: WatchStatus
    personal_rating: int | None
    tier: TierRank | None
    external_rating: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
