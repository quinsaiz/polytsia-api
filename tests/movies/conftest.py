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


def mock_tmdb_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)

    if "/search/movie" in url:
        return httpx.Response(
            200,
            json={
                "page": 1,
                "results": [
                    {
                        "id": 155,
                        "title": "The Dark Knight",
                        "overview": "Batman movie",
                        "release_date": "2008-07-16",
                        "poster_path": "/poster.jpg",
                        "vote_average": 8.5,
                        "genre_ids": [28, 80],
                    }
                ],
                "total_pages": 1,
                "total_results": 1,
            },
        )
    if "/genre/movie/list" in url:
        return httpx.Response(
            200,
            json={
                "genres": [{"id": 28, "name": "Action"}, {"id": 80, "name": "Crime"}]
            },
        )
    if "/movie/155" in url:
        return httpx.Response(
            200,
            json={
                "id": 155,
                "title": "The Dark Knight",
                "overview": "Batman movie",
                "release_date": "2008-07-16",
                "poster_path": "/poster.jpg",
                "vote_average": 8.5,
                "genres": [{"id": 28, "name": "Action"}],
            },
        )
    if "/movie/999999" in url:
        return httpx.Response(404, json={"status_message": "not found"})

    return httpx.Response(404)


@pytest_asyncio.fixture
async def client_with_mock_tmdb(client: AsyncClient) -> AsyncGenerator[AsyncClient]:
    transport = httpx.MockTransport(mock_tmdb_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    app.dependency_overrides[get_http_client] = lambda: mock_client

    yield client

    await mock_client.aclose()
    del app.dependency_overrides[get_http_client]


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    user = User(
        email="movieuser@example.com",
        username="movieuser",
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
