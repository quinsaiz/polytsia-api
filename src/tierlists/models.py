import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    case,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.games.models import UserGame
from src.movies.models import UserMovie


class TierList(Base):
    __tablename__ = "tier_lists"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    media_type: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
    )

    items: Mapped[list["TierListItem"]] = relationship(
        back_populates="tier_list",
        cascade="all, delete-orphan",
        order_by=lambda: (
            case(
                {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5},
                value=TierListItem.tier,
                else_=6,
            ),
            TierListItem.position,
            TierListItem.id,
        ),
    )

    def __repr__(self) -> str:
        return f"TierList(id={self.id}, name={self.name}, media_type={self.media_type})"


class TierListItem(Base):
    __tablename__ = "tier_list_items"
    __table_args__ = (
        UniqueConstraint("tier_list_id", "user_movie_id", name="uq_tierlist_movie"),
        UniqueConstraint("tier_list_id", "user_game_id", name="uq_tierlist_game"),
        UniqueConstraint(
            "tier_list_id",
            "tier",
            "position",
            name="uq_tierlist_tier_position",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("position >= 0", name="ck_tier_item_position_nonnegative"),
        CheckConstraint(
            "(user_movie_id IS NOT NULL AND user_game_id IS NULL) OR "
            "(user_movie_id IS NULL AND user_game_id IS NOT NULL)",
            name="ck_tier_item_single_media_ref",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    tier_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("tier_lists.id", ondelete="CASCADE"),
        index=True,
    )
    user_movie_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user_movies.id", ondelete="CASCADE"),
    )
    user_game_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey("user_games.id", ondelete="CASCADE"),
    )
    tier: Mapped[str] = mapped_column(String(1))
    position: Mapped[int] = mapped_column(Integer, default=0)

    tier_list: Mapped[TierList] = relationship(back_populates="items")
    movie: Mapped[UserMovie | None] = relationship(lazy="raise")
    game: Mapped[UserGame | None] = relationship(lazy="raise")

    @property
    def summary(self) -> dict[str, object]:
        tracked = self.movie if self.user_movie_id is not None else self.game
        assert tracked is not None
        return {
            "media_type": "movie" if self.user_movie_id is not None else "game",
            "tracked_id": tracked.id,
            "catalog_id": tracked.tmdb_id
            if isinstance(tracked, UserMovie)
            else tracked.rawg_id,
            "catalog_title": tracked.catalog_title,
            "catalog_poster_path": tracked.catalog_poster_path
            if isinstance(tracked, UserMovie)
            else None,
            "catalog_background_image": tracked.catalog_background_image
            if isinstance(tracked, UserGame)
            else None,
            "catalog_release_date": tracked.catalog_release_date,
            "catalog_metadata_fetched_at": tracked.catalog_metadata_fetched_at,
            "external_rating": tracked.external_rating,
        }

    def __repr__(self) -> str:
        return f"TierListItem(id={self.id}, tier={self.tier}, position={self.position})"
