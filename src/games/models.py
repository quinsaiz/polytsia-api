import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.games.constants import PlayStatus


class UserGame(Base):
    __tablename__ = "user_games"
    __table_args__ = (UniqueConstraint("user_id", "rawg_id", name="uq_user_game"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    rawg_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default=PlayStatus.PLANNED)
    personal_rating: Mapped[int | None] = mapped_column(Integer)
    tier: Mapped[str | None] = mapped_column(String(1))
    notes: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"UserGame user_id={self.user_id}, rawg_id={self.rawg_id}"
