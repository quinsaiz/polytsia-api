import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from fastapi.exceptions import HTTPException, RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def import_all_models() -> None:
    """Import all models so Alembic can detect them for autogenerate."""
    from src.auth.models import RefreshToken, User
    from src.games.models import UserGame
    from src.movies.models import UserMovie
    from src.tierlists.models import TierList, TierListItem


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except SQLAlchemyError:
            logger.error("Database session error", exc_info=True)
            await session.rollback()
            raise
        except (HTTPException, RequestValidationError):
            await session.rollback()
            raise
        except Exception:
            logger.error("Unhandled exception during request", exc_info=True)
            await session.rollback()
            raise


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
