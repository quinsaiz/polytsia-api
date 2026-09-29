import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from src.games.constants import PlayStatus, TierRank


class RAWGGenreSchema(BaseModel):
    id: int
    name: str


class RAWGPlatformSchema(BaseModel):
    id: int
    name: str


class RAWGPlatformWrapperSchema(BaseModel):
    platform: RAWGPlatformSchema


class RAWGGameSchema(BaseModel):
    id: int
    name: str
    description_raw: str = ""
    released: str | None = None
    background_image: str | None = None
    rating: float
    genres: list[RAWGGenreSchema] = []
    platforms: list[RAWGPlatformWrapperSchema] = []


class RAWGSearchResultSchema(BaseModel):
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[RAWGGameSchema] = []


class RAWGGenreListSchema(BaseModel):
    results: list[RAWGGenreSchema]


class RAWGPlatformListSchema(BaseModel):
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[RAWGPlatformSchema]


class TrackGameSchema(BaseModel):
    rawg_id: int
    status: PlayStatus = PlayStatus.PLANNED


class UpdateUserGameSchema(BaseModel):
    status: PlayStatus | None = None
    personal_rating: int | None = Field(default=None, ge=0, le=10)
    tier: TierRank | None = None
    notes: str | None = None


class UserGameResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    rawg_id: int
    status: PlayStatus
    personal_rating: int | None
    tier: TierRank | None
    external_rating: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
