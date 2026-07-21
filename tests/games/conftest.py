from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import hash_password
from src.config import settings
from src.dependencies import get_http_client
from src.main import app


@pytest_asyncio.fixture(autouse=True)
async def clear_redis_cache() -> AsyncGenerator[None]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    yield
    await client.aclose()


def mock_rawg_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)

    if "/games/155" in url:
        return httpx.Response(
            200,
            json={
                "id": 155,
                "name": "The Witcher 3: Wild Hunt",
                "description_raw": "An open world RPG",
                "released": "2015-05-19",
                "background_image": "https://example.com/witcher.jpg",
                "rating": 4.66,
                "genres": [{"id": 4, "name": "Action"}],
                "platforms": [
                    {"platform": {"id": 1, "name": "PC"}},
                ],
            },
        )
    if "/games/999999" in url:
        return httpx.Response(404, json={"detail": "Not found."})
    if "/games" in url:  # search — matched AFTER /games/155 to avoid false positive
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 155,
                        "name": "The Witcher 3: Wild Hunt",
                        "description_raw": "An open world RPG",
                        "released": "2015-05-19",
                        "background_image": "https://example.com/witcher.jpg",
                        "rating": 4.66,
                        "genres": [{"id": 4, "name": "Action"}],
                        "platforms": [{"platform": {"id": 1, "name": "PC"}}],
                    }
                ],
            },
        )
    if "/genres" in url:
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 4, "name": "Action"}],
            },
        )
    if "/platforms" in url:
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 1, "name": "PC"}],
            },
        )

    return httpx.Response(404)


@pytest_asyncio.fixture
async def client_with_mock_rawg(client: AsyncClient) -> AsyncGenerator[AsyncClient]:
    transport = httpx.MockTransport(mock_rawg_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    app.dependency_overrides[get_http_client] = lambda: mock_client

    yield client

    await mock_client.aclose()
    del app.dependency_overrides[get_http_client]


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    user = User(
        email="gameuser@example.com",
        username="gameuser",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user: User) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": test_user.email,
            "password": "password123",
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
