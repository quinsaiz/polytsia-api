import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from src.tierlists.constants import MediaType, TierRank


class RenameTierListSchema(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CreateTierListSchema(RenameTierListSchema):
    media_type: MediaType


class AddTierListItemSchema(BaseModel):
    user_movie_id: uuid.UUID | None = None
    user_game_id: uuid.UUID | None = None
    tier: TierRank = TierRank.C
    position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exactly_one_ref(self) -> "AddTierListItemSchema":
        if (self.user_movie_id is None) == (self.user_game_id is None):
            raise ValueError("Exactly one of user_movie_id or user_game_id must be set")
        return self


class MoveTierListItemSchema(BaseModel):
    tier: TierRank
    position: int | None = Field(default=None, ge=0)


class TierListItemSummarySchema(BaseModel):
    media_type: MediaType
    tracked_id: uuid.UUID
    catalog_id: int
    catalog_title: str | None
    catalog_poster_path: str | None
    catalog_background_image: str | None
    catalog_release_date: date | None
    catalog_metadata_fetched_at: datetime | None
    external_rating: float | None


class TierListItemResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_movie_id: uuid.UUID | None
    user_game_id: uuid.UUID | None
    tier: TierRank
    position: int
    summary: TierListItemSummarySchema


class TierListResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    media_type: MediaType
    items: list[TierListItemResponseSchema] = []
    created_at: datetime
    updated_at: datetime
