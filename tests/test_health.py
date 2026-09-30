import asyncio
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src import main
from tests.conftest import test_engine


@pytest.fixture
async def health_client() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def test_liveness_never_checks_dependencies(
    health_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = MagicMock()
    engine.connect.side_effect = AssertionError("Liveness must not access PostgreSQL")
    redis = MagicMock(side_effect=AssertionError("Liveness must not access Redis"))
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr("src.redis.get_redis_client", redis)
    response = await health_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    engine.connect.assert_not_called()
    redis.assert_not_called()


async def test_readiness_queries_postgres(
    health_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "engine", test_engine)
    response = await health_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_missing_database_returns_503_and_liveness_stays_200(
    health_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reserve a local port without listening; never stop the shared test database.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        url = test_engine.url.set(host="127.0.0.1", port=unavailable.getsockname()[1])
        engine = create_async_engine(url)
        monkeypatch.setattr(main, "engine", engine)
        try:
            response = await asyncio.wait_for(health_client.get("/ready"), timeout=2)
            assert response.status_code == 503
            assert response.json() == {"detail": "Database is unavailable"}
            assert (await health_client.get("/health")).status_code == 200
        finally:
            await engine.dispose()
    monkeypatch.setattr(main, "engine", test_engine)
    assert (await health_client.get("/ready")).status_code == 200


@pytest.mark.parametrize("phase", ["acquire", "query"])
async def test_readiness_timeout_covers_acquisition_and_query(
    phase: str, health_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = asyncio.Event()

    async def hang(*args: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    connection = AsyncMock()
    connection.execute.side_effect = hang

    @asynccontextmanager
    async def connect() -> AsyncGenerator[AsyncMock]:
        if phase == "acquire":
            await hang()
        yield connection

    engine = MagicMock()
    engine.connect.side_effect = connect
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "READINESS_TIMEOUT_SECONDS", 0.02)
    response = await asyncio.wait_for(health_client.get("/ready"), timeout=0.5)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert cancelled.is_set()


async def test_readiness_error_does_not_expose_connection_details(
    health_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = MagicMock()
    secret = "postgresql://user:secret-sentinel@private-host/database"
    engine.connect.side_effect = OSError(secret)
    monkeypatch.setattr(main, "engine", engine)
    response = await health_client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert secret not in response.text + caplog.text
