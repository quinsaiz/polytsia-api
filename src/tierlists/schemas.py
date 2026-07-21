import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator

from src.tierlists.constants import MediaType, TierRank


class CreateTierListSchema(BaseModel):
    name: str
    media_type: MediaType


class AddTierListItemSchema(BaseModel):
    user_movie_id: uuid.UUID | None = None
    user_game_id: uuid.UUID | None = None
    tier: TierRank = TierRank.C
    position: int = 0

    @model_validator(mode="after")
    def exactly_one_ref(self) -> "AddTierListItemSchema":
        if (self.user_movie_id is None) == (self.user_game_id is None):
            raise ValueError("Exactly one of user_movie_id or user_game_id must be set")
        return self


class MoveTierListItemSchema(BaseModel):
    tier: TierRank
    position: int


class TierListItemResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_movie_id: uuid.UUID | None
    user_game_id: uuid.UUID | None
    tier: TierRank
    position: int


class TierListResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    media_type: MediaType
    items: list[TierListItemResponseSchema] = []
    created_at: datetime
    updated_at: datetime
