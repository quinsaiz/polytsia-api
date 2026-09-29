from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from redis.asyncio import Redis

from src.config import settings
from src.dependencies import get_http_client
from src.main import app

SENTINEL = "RAWG_SECRET_SENTINEL_7"


@pytest_asyncio.fixture
async def rawg_client(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[AsyncClient, list[httpx.Request]]]:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(settings, "rawg_api_key", SENTINEL)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get("page", "1"))
        size = int(request.url.params.get("page_size", "1"))
        upstream_link = f"https://api.rawg.io/api{request.url.path}?key={SENTINEL}"
        return httpx.Response(
            200,
            json={
                "count": 30,
                "next": (
                    f"{upstream_link}&page={page + 1}"
                    if page < 3 or page == 100
                    else None
                ),
                "previous": f"{upstream_link}&page={page - 1}" if page > 1 else None,
                "results": (
                    [
                        {"id": page * 100 + index, "name": "Game", "rating": 4.0}
                        for index in range(size)
                    ]
                    if request.url.path.endswith("/games")
                    else [{"id": page, "name": f"Platform {page}"}]
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        app.dependency_overrides[get_http_client] = lambda: mock_client
        try:
            yield client, requests
        finally:
            del app.dependency_overrides[get_http_client]


async def _assert_cache_has_no_key(pattern: str) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        keys = await redis.keys(pattern)
        assert keys
        for key in keys:
            stored = await redis.get(key)
            assert stored is not None
            assert SENTINEL not in stored
            assert "api.rawg.io" not in stored
    finally:
        await redis.aclose()


async def test_search_links_are_local_on_miss_and_hit(
    rawg_client: tuple[AsyncClient, list[httpx.Request]],
) -> None:
    client, requests = rawg_client
    params: dict[str, str | int] = {
        "query": "Space & Quest",
        "page": 1,
        "page_size": 2,
    }

    first = await client.get("/api/v1/games/search", params=params)
    assert first.status_code == 200
    assert SENTINEL not in first.text
    assert first.json()["previous"] is None
    link = first.json()["next"]
    assert urlsplit(link).path == "/api/v1/games/search"
    assert parse_qs(urlsplit(link).query) == {
        "query": ["Space & Quest"],
        "page": ["2"],
        "page_size": ["2"],
    }

    second = await client.get(link)
    assert second.status_code == 200
    assert SENTINEL not in second.text
    previous = second.json()["previous"]
    assert parse_qs(urlsplit(previous).query)["page"] == ["1"]
    assert len(requests) == 2
    assert all(request.url.params["key"] == SENTINEL for request in requests)

    cached = await client.get("/api/v1/games/search", params=params)
    assert cached.json() == first.json()
    assert SENTINEL not in cached.text
    assert len(requests) == 2
    await _assert_cache_has_no_key("rawg:search:v2:*")


async def test_platform_links_are_local_on_miss_and_hit(
    rawg_client: tuple[AsyncClient, list[httpx.Request]],
) -> None:
    client, requests = rawg_client

    first = await client.get("/api/v1/games/platforms")
    assert first.status_code == 200
    assert first.json()["next"] == "/api/v1/games/platforms?page=2"
    assert first.json()["previous"] is None
    assert SENTINEL not in first.text

    second = await client.get(first.json()["next"])
    assert second.status_code == 200
    assert second.json()["previous"] == "/api/v1/games/platforms?page=1"
    assert second.json()["results"][0]["id"] == 2
    assert SENTINEL not in second.text
    assert [request.url.params["page"] for request in requests] == ["1", "2"]

    cached = await client.get("/api/v1/games/platforms?page=2")
    assert cached.json() == second.json()
    assert SENTINEL not in cached.text
    assert len(requests) == 2
    await _assert_cache_has_no_key("rawg:platforms:v2:*")


async def test_search_last_allowed_page_has_no_invalid_next_link(
    rawg_client: tuple[AsyncClient, list[httpx.Request]],
) -> None:
    client, requests = rawg_client
    response = await client.get(
        "/api/v1/games/search", params={"query": "Space", "page": 100}
    )
    assert response.status_code == 200
    assert response.json()["next"] is None
    assert len(requests) == 1


@pytest.mark.parametrize("sizes", [(1, 3), (3, 1)])
async def test_search_cache_separates_page_sizes_in_both_orders(
    rawg_client: tuple[AsyncClient, list[httpx.Request]], sizes: tuple[int, int]
) -> None:
    client, requests = rawg_client
    for size in sizes:
        response = await client.get(
            "/api/v1/games/search",
            params={"query": "same", "page": 1, "page_size": size},
        )
        assert response.status_code == 200
        assert len(response.json()["results"]) == size
        assert parse_qs(urlsplit(response.json()["next"]).query)["page_size"] == [
            str(size)
        ]
        assert SENTINEL not in response.text

    assert len(requests) == 2
    for size in reversed(sizes):
        response = await client.get(
            "/api/v1/games/search",
            params={"query": "same", "page": 1, "page_size": size},
        )
        assert len(response.json()["results"]) == size
        assert SENTINEL not in response.text

    assert len(requests) == 2
    await _assert_cache_has_no_key("rawg:search:v2:*")
