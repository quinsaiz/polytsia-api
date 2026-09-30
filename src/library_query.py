"""Shared, SQL-only controls for the two private libraries."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Select, select

from src.games.constants import PlayStatus
from src.games.models import UserGame
from src.movies.constants import TierRank, WatchStatus
from src.movies.models import UserMovie


class LibraryQuery(BaseModel):
    tier: TierRank | None = None
    rated: bool | None = None
    personal_rating: int | None = Field(default=None, ge=0, le=10)
    query: str | None = Field(default=None, min_length=1, max_length=200)
    sort_by: Literal["created_at", "personal_rating", "catalog_title"] = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"


class MovieLibraryQuery(LibraryQuery):
    status: WatchStatus | None = None


class GameLibraryQuery(LibraryQuery):
    status: PlayStatus | None = None


def library_statement[T: (UserMovie, UserGame)](
    model: type[T], user_id: uuid.UUID, options: MovieLibraryQuery | GameLibraryQuery
) -> Select[tuple[T]]:
    stmt = select(model).where(model.user_id == user_id)
    if options.status is not None:
        stmt = stmt.where(model.status == options.status)
    if options.tier is not None:
        stmt = stmt.where(model.tier == options.tier)
    if options.rated is not None:
        stmt = stmt.where(
            model.personal_rating.is_not(None)
            if options.rated
            else model.personal_rating.is_(None)
        )
    if options.personal_rating is not None:
        stmt = stmt.where(model.personal_rating == options.personal_rating)
    if options.query is not None:
        stmt = stmt.where(model.catalog_title.icontains(options.query, autoescape=True))
    column = {
        "created_at": model.created_at,
        "personal_rating": model.personal_rating,
        "catalog_title": model.catalog_title,
    }[options.sort_by]
    order = column.asc() if options.sort_order == "asc" else column.desc()
    return stmt.order_by(order.nulls_last(), model.id.asc())
