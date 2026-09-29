import asyncio
import json
import os
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.config import settings
from src.games.models import UserGame
from src.movies.models import UserMovie
from src.recommendations import service
from src.recommendations.bootstrap import enqueue_missing_candidate_pools
from src.recommendations.constants import (
    CANDIDATES_CACHE_TTL,
    GAME_CANDIDATES_CACHE_KEY,
    MOVIE_CANDIDATES_CACHE_KEY,
)
from src.recommendations.tasks import (
    refresh_game_candidates,
    refresh_movie_candidates,
)
from src.redis import cache_get, redis_pool


@pytest.fixture
def isolated_recommendation_cache(
    candidate_pool: dict[str, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("POLYTSIA_TEST_ISOLATED_SERVICES") != "1":
        pytest.fail(
            "Run pipeline tests with ./scripts/test.sh so PostgreSQL and Redis "
            "are isolated from development data.",
            pytrace=False,
        )
    db_url = urlsplit(settings.test_database_url)
    redis_url = urlsplit(settings.redis_url)
    broker_url = urlsplit(settings.celery_broker_url)
    assert (db_url.hostname, db_url.port, db_url.path) == (
        "test-db",
        5432,
        "/polytsia_test",
    )
    assert (redis_url.hostname, redis_url.port, redis_url.path) == (
        "test-redis",
        6379,
        "/0",
    )
    assert (broker_url.hostname, broker_url.port, broker_url.path) == (
        "test-redis",
        6379,
        "/1",
    )
    monkeypatch.setattr(service, "cache_get", cache_get)


async def test_tasks_write_real_redis_and_api_filters_candidates(
    isolated_recommendation_cache: None,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    auth_headers: dict[str, str],
    db: AsyncSession,
    test_user: User,
) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        assert await redis.dbsize() == 0

        db.add_all(
            [
                UserMovie(
                    user_id=test_user.id,
                    tmdb_id=tmdb_id,
                    status="completed",
                    tier="A",
                    personal_rating=8,
                    external_rating=8.0,
                )
                for tmdb_id in (1, 2, 3)
            ]
            + [
                UserGame(
                    user_id=test_user.id,
                    rawg_id=rawg_id,
                    status="completed",
                    tier="A",
                    personal_rating=8,
                    external_rating=4.0,
                )
                for rawg_id in (11, 12, 13)
            ]
        )
        await db.commit()

        initial_response = await client.get(
            "/api/v1/recommendations/", headers=auth_headers
        )
        assert initial_response.status_code == 200
        assert initial_response.json()["movies_pool_available"] is False
        assert initial_response.json()["games_pool_available"] is False
        await redis_pool.disconnect()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/discover/movie"):
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {"id": 100, "title": "Recommended", "vote_average": 8.5},
                            {
                                "id": 101,
                                "title": "Below threshold",
                                "vote_average": 7.0,
                            },
                            {"id": 1, "title": "Already tracked", "vote_average": 9.0},
                        ]
                    },
                )
            assert request.url.path.endswith("/games")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 200,
                            "name": "Recommended",
                            "rating": 4.5,
                            "ratings_count": 700,
                        },
                        {
                            "id": 201,
                            "name": "Below threshold",
                            "rating": 3.0,
                            "ratings_count": 700,
                        },
                        {
                            "id": 11,
                            "name": "Already tracked",
                            "rating": 5.0,
                            "ratings_count": 700,
                        },
                    ]
                },
            )

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr("src.recommendations.tasks.httpx.AsyncClient", mock_client)

        movie_result = await asyncio.to_thread(
            refresh_movie_candidates.apply, throw=False
        )
        game_result = await asyncio.to_thread(
            refresh_game_candidates.apply, throw=False
        )
        assert movie_result.successful(), movie_result.result
        assert game_result.successful(), game_result.result

        assert set(await redis.keys("*")) == {
            GAME_CANDIDATES_CACHE_KEY,
            MOVIE_CANDIDATES_CACHE_KEY,
        }
        movie_raw = await redis.get(MOVIE_CANDIDATES_CACHE_KEY)
        game_raw = await redis.get(GAME_CANDIDATES_CACHE_KEY)
        assert movie_raw is not None
        assert game_raw is not None
        movie_pool = json.loads(movie_raw)
        game_pool = json.loads(game_raw)
        assert [item["tmdb_id"] for item in movie_pool] == [100, 101, 1]
        assert [item["rawg_id"] for item in game_pool] == [200, 201, 11]
        assert 0 < await redis.ttl(MOVIE_CANDIDATES_CACHE_KEY) <= CANDIDATES_CACHE_TTL
        assert 0 < await redis.ttl(GAME_CANDIDATES_CACHE_KEY) <= CANDIDATES_CACHE_TTL

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["movie_threshold"] == pytest.approx(8.0)
        assert data["game_threshold"] == pytest.approx(4.0)
        assert data["movies_pool_available"] is True
        assert data["games_pool_available"] is True
        assert [movie["tmdb_id"] for movie in data["movies"]] == [100]
        assert [game["rawg_id"] for game in data["games"]] == [200]

        await redis.pexpire(MOVIE_CANDIDATES_CACHE_KEY, 1)
        await asyncio.sleep(0.02)
        expired_response = await client.get(
            "/api/v1/recommendations/", headers=auth_headers
        )
        assert expired_response.status_code == 200
        assert expired_response.json()["movies_pool_available"] is False
        assert expired_response.json()["games_pool_available"] is True
        assert expired_response.json()["movies"] == []
    finally:
        await redis.delete(MOVIE_CANDIDATES_CACHE_KEY, GAME_CANDIDATES_CACHE_KEY)
        await redis.aclose()


async def test_bootstrap_queues_missing_pools_and_can_be_rerun(
    isolated_recommendation_cache: None,
) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    broker = Redis.from_url(settings.celery_broker_url, decode_responses=True)
    try:
        assert await redis.dbsize() == 0
        assert await broker.llen("celery") == 0

        assert await enqueue_missing_candidate_pools() == ["movies", "games"]
        assert await broker.llen("celery") == 2

        await redis.set(MOVIE_CANDIDATES_CACHE_KEY, "[]", ex=CANDIDATES_CACHE_TTL)
        await redis.set(GAME_CANDIDATES_CACHE_KEY, "[]", ex=CANDIDATES_CACHE_TTL)
        assert await enqueue_missing_candidate_pools() == []
        assert await broker.llen("celery") == 2

        await redis.pexpire(GAME_CANDIDATES_CACHE_KEY, 1)
        await asyncio.sleep(0.02)
        assert await enqueue_missing_candidate_pools() == ["games"]
        assert await broker.llen("celery") == 3
    finally:
        await redis.delete(MOVIE_CANDIDATES_CACHE_KEY, GAME_CANDIDATES_CACHE_KEY)
        await broker.delete("celery")
        await redis.aclose()
        await broker.aclose()
