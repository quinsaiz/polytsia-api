import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from src.movies.constants import TierRank, WatchStatus


class TMDBGenreSchema(BaseModel):
    id: int
    name: str


class TMDBMovieSchema(BaseModel):
    id: int
    title: str
    overview: str
    release_date: str | None = None
    poster_path: str | None = None
    vote_average: float = Field(allow_inf_nan=False)
    genre_ids: list[int] = []

    @model_validator(mode="before")
    @classmethod
    def extract_genre_ids(cls, data: object) -> object:
        """
        TMDB's /search/movie returns genre_ids: [28, 80].
        TMDB's /movie/{id} returns genres: [{"id": 28, "name": "Action"}].
        Normalize both into genre_ids so the schema works for both endpoints.
        """
        if not isinstance(data, dict):
            return data
        if "genre_ids" not in data and "genres" in data:
            genres = data["genres"]
            if not isinstance(genres, list):
                raise ValueError("Expected a genre list")
            return {
                **data,
                "genre_ids": [
                    TMDBGenreSchema.model_validate(genre).id for genre in genres
                ],
            }
        return data


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
    catalog_title: str | None = None
    catalog_poster_path: str | None = None
    catalog_release_date: date | None = None
    catalog_metadata_fetched_at: datetime | None = None
    status: WatchStatus
    personal_rating: int | None
    tier: TierRank | None
    external_rating: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
