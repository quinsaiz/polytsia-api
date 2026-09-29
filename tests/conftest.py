import os
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.database import Base, get_db
from src.main import app
from src.redis import redis_pool

test_engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
TestSessionFactory = async_sessionmaker(test_engine, expire_on_commit=False)


def pytest_sessionstart(session: pytest.Session) -> None:
    if not _isolated_services_configured():
        raise pytest.UsageError(
            "Run the full test suite with ./scripts/test.sh. It starts isolated "
            "PostgreSQL and Redis; ordinary development Redis must not be used "
            "because movie/game fixtures call FLUSHDB."
        )


def _isolated_services_configured() -> bool:
    if os.environ.get("POLYTSIA_TEST_ISOLATED_SERVICES") != "1":
        return False
    db_url = urlsplit(settings.test_database_url)
    redis_url = urlsplit(settings.redis_url)
    broker_url = urlsplit(settings.celery_broker_url)
    return (
        (db_url.hostname, db_url.port, db_url.path)
        == ("test-db", 5432, "/polytsia_test")
        and (redis_url.hostname, redis_url.port, redis_url.path)
        == ("test-redis", 6379, "/0")
        and (broker_url.hostname, broker_url.port, broker_url.path)
        == ("test-redis", 6379, "/1")
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def setup_database() -> AsyncGenerator[None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield session
        await session.close()
        await transaction.rollback()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_pool() -> AsyncGenerator[None]:
    yield
    await redis_pool.disconnect()
