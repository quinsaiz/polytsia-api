import socket
from collections.abc import AsyncGenerator

import httpx
import pytest
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from src import main
from src import redis as cache
from src.dependencies import get_http_client
from tests.conftest import test_engine


@pytest.fixture
async def unavailable_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None]:
    # Connection refusal on a private port; development/test Redis is untouched.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        pool = ConnectionPool.from_url(
            f"redis://127.0.0.1:{unavailable.getsockname()[1]}/0",
            decode_responses=True,
        )
        monkeypatch.setattr(cache, "redis_pool", pool)
        redis = Redis(connection_pool=pool)
        try:
            with pytest.raises(RedisConnectionError):
                await redis.ping()
            yield
        finally:
            await redis.aclose()
            await pool.disconnect()


async def test_api_lifespan_db_routes_and_recommendations_without_redis(
    unavailable_redis: None,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_setup_logging", lambda: None)
    monkeypatch.setattr(main, "engine", test_engine)
    async with main.app.router.lifespan_context(main.app):
        assert not main.app.state.http_client.is_closed
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200
        user = {
            "email": "outage@example.com",
            "username": "outage",
            "password": "password123",
            "password_confirm": "password123",
        }
        assert (
            await client.post("/api/v1/auth/register", json=user)
        ).status_code == 201
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": user["email"], "password": user["password"]},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        profile = await client.get("/api/v1/auth/me", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["email"] == user["email"]
        for media in ("movies", "games"):
            tracked = await client.get(f"/api/v1/{media}/", headers=headers)
            assert tracked.status_code == 200
            assert tracked.json()["total"] == 0
            recommendations = await client.get(
                f"/api/v1/recommendations/{media}", headers=headers
            )
            assert recommendations.status_code == 200
            assert recommendations.json()["pool_available"] is False
            assert recommendations.json()[media] == []
        combined = await client.get("/api/v1/recommendations/", headers=headers)
        assert combined.status_code == 200
        for media in ("movies", "games"):
            assert combined.json()[f"{media}_pool_available"] is False
            assert combined.json()[media] == []
    assert main.app.state.http_client.is_closed


async def test_catalog_cache_degrades_to_miss_without_redis(
    unavailable_redis: None, client: httpx.AsyncClient
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/genre/movie/list"):
            return httpx.Response(200, json={"genres": [{"id": 1, "name": "Drama"}]})
        assert request.url.path.endswith("/genres")
        return httpx.Response(200, json={"results": [{"id": 1, "name": "RPG"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        main.app.dependency_overrides[get_http_client] = lambda: upstream
        try:
            for _ in range(2):
                for media, key in (("movies", "genres"), ("games", "results")):
                    response = await client.get(f"/api/v1/{media}/genres")
                    assert response.status_code == 200
                    assert response.json()[key][0]["id"] == 1
        finally:
            main.app.dependency_overrides.pop(get_http_client, None)
    assert len(requests) == 4
    # Required writes used by workers must still fail during the outage.
    with pytest.raises(RedisConnectionError):
        await cache.cache_set_required("outage-test", [], 60)
