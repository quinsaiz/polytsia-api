from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.config import settings
from src.recommendations.constants import CANDIDATE_POOL_SIZE, MAX_CANDIDATE_PAGES
from src.recommendations.tasks import (
    _fetch_game_candidates,
    _fetch_movie_candidates,
    refresh_game_candidates,
    refresh_movie_candidates,
)
from src.upstream import InvalidUpstreamPayload

if TYPE_CHECKING:
    from celery import Task


@pytest.fixture
def setup_database() -> None:
    """These HTTP-only tests do not need the global database setup fixture."""


def _patch_tasks_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    original_client = httpx.AsyncClient

    def mock_client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(
        "src.recommendations.tasks.httpx.AsyncClient", mock_client_factory
    )


class TestFetchMovieCandidates:
    async def test_parses_tmdb_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/discover/movie")
            assert request.url.params["page"] == "1"
            assert request.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 550,
                            "title": "Fight Club",
                            "vote_average": 8.4,
                            "overview": "An insomniac office worker...",
                            "genre_ids": [18],
                            "poster_path": "/poster.jpg",
                        }
                    ]
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        candidates = await _fetch_movie_candidates()
        assert candidates == [
            {
                "tmdb_id": 550,
                "title": "Fight Club",
                "rating": 8.4,
                "overview": "An insomniac office worker...",
                "genre_ids": [18],
                "poster_path": "/poster.jpg",
            }
        ]

    async def test_empty_tmdb_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_tasks_client(
            monkeypatch,
            httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []})),
        )

        assert await _fetch_movie_candidates() == []

    async def test_collects_pages_until_pool_is_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            requested_pages.append(page)
            return httpx.Response(
                200,
                json={
                    "total_pages": 10,
                    "results": [
                        {
                            "id": (page - 1) * 20 + index,
                            "title": "Movie",
                            "vote_average": 8.0,
                        }
                        for index in range(1, 21)
                    ],
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        candidates = await _fetch_movie_candidates()
        assert [movie["tmdb_id"] for movie in candidates] == list(range(1, 101))
        assert requested_pages == [1, 2, 3, 4, 5]

    async def test_stops_at_last_page_and_deduplicates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            requested_pages.append(page)
            ids = [1, 2] if page == 1 else [2, 3]
            return httpx.Response(
                200,
                json={
                    "total_pages": 2,
                    "results": [
                        {"id": movie_id, "title": "Movie", "vote_average": 8.0}
                        for movie_id in ids
                    ],
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert [movie["tmdb_id"] for movie in await _fetch_movie_candidates()] == [
            1,
            2,
            3,
        ]
        assert requested_pages == [1, 2]


class TestFetchGameCandidates:
    async def test_filters_rawg_rating_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/games")
            assert request.url.params["ordering"] == "-rating"
            assert request.url.params["page_size"] == str(CANDIDATE_POOL_SIZE)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 155,
                            "name": "The Witcher 3",
                            "rating": 4.66,
                            "ratings_count": 700,
                            "background_image": "/witcher.jpg",
                        },
                        {
                            "id": 156,
                            "name": "Little Known Game",
                            "rating": 4.9,
                            "ratings_count": 10,
                        },
                    ]
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert await _fetch_game_candidates() == [
            {
                "rawg_id": 155,
                "name": "The Witcher 3",
                "rating": 4.66,
                "background_image": "/witcher.jpg",
            }
        ]

    async def test_follows_pages_and_deduplicates_after_filtering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            requested_pages.append(page)
            if page == 1:
                results = [
                    {"id": 1, "name": "One", "rating": 4.5, "ratings_count": 700},
                    {
                        "id": 2,
                        "name": "Too few votes",
                        "rating": 5.0,
                        "ratings_count": 5,
                    },
                ]
            else:
                results = [
                    {"id": 1, "name": "One", "rating": 4.5, "ratings_count": 700},
                    {"id": 3, "name": "Three", "rating": 4.2, "ratings_count": 800},
                ]
            return httpx.Response(
                200,
                json={"results": results, "next": "more" if page == 1 else None},
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert [game["rawg_id"] for game in await _fetch_game_candidates()] == [1, 3]
        assert requested_pages == [1, 2]

    async def test_full_pool_does_not_request_another_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_pages.append(int(request.url.params["page"]))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": game_id,
                            "name": "Game",
                            "rating": 4.0,
                            "ratings_count": 700,
                        }
                        for game_id in range(CANDIDATE_POOL_SIZE)
                    ],
                    "next": "more",
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert len(await _fetch_game_candidates()) == CANDIDATE_POOL_SIZE
        assert requested_pages == [1]

    async def test_repeated_page_stops_without_looping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_pages.append(int(request.url.params["page"]))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": 1, "name": "Same", "rating": 4.0, "ratings_count": 700}
                    ],
                    "next": "more",
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert [game["rawg_id"] for game in await _fetch_game_candidates()] == [1]
        assert requested_pages == [1, 2]

    async def test_page_limit_bounds_nonending_catalogue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            requested_pages.append(page)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": page,
                            "name": "Game",
                            "rating": 4.0,
                            "ratings_count": 700,
                        }
                    ],
                    "next": "more",
                },
            )

        _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))

        assert len(await _fetch_game_candidates()) == MAX_CANDIDATE_PAGES
        assert requested_pages == list(range(1, MAX_CANDIDATE_PAGES + 1))


@pytest.mark.parametrize(
    ("task", "path", "candidate", "expected_attempts"),
    [
        (
            refresh_movie_candidates,
            "/discover/movie",
            {"id": 101, "title": "Movie", "vote_average": 8.5},
            4,
        ),
        (
            refresh_game_candidates,
            "/games",
            {"id": 201, "name": "Game", "rating": 4.5, "ratings_count": 700},
            6,
        ),
    ],
)
def test_redis_write_failure_retries_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    task: Task[..., None],
    path: str,
    candidate: dict[str, object],
    expected_attempts: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(path)
        return httpx.Response(200, json={"results": [candidate]})

    _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))
    attempts = 0

    class FailingRedis:
        async def set(self, *args: object, **kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            raise RedisConnectionError("isolated test write refusal")

    monkeypatch.setattr("src.redis.get_redis_client", FailingRedis)
    result = task.apply(throw=False)

    assert result.failed(), (
        f"Task incorrectly reported {result.state} after a failed write"
    )
    assert isinstance(result.result, RedisConnectionError)
    assert attempts == expected_attempts


def _invalid_candidate_body(media, candidate, failure, secret_url):
    candidate = dict(candidate)
    raw_bodies = {
        "json": f'{{"url":"{secret_url}",'.encode(),
        "encoding": b"\xff",
        "root": json.dumps([secret_url]).encode(),
    }
    if failure in raw_bodies:
        return raw_bodies[failure]
    name = "title" if media == "movies" else "name"
    rating = "vote_average" if media == "movies" else "rating"
    metadata = "total_pages" if media == "movies" else "next"
    missing_fields = {
        "missing_id": "id",
        "missing_name": name,
        "missing_rating": rating,
    }
    if failure in missing_fields:
        del candidate[missing_fields[failure]]
        payload = {"results": [candidate]}
    else:
        nested = (
            {**candidate, "genre_ids": [{"url": secret_url}]}
            if media == "movies"
            else {
                key: value for key, value in candidate.items() if key != "ratings_count"
            }
        )
        payload = {
            "missing_results": {},
            "results_type": {"results": {"url": secret_url}},
            "item_type": {"results": [secret_url]},
            "rating_type": {"results": [{**candidate, rating: secret_url}]},
            "nonfinite_rating": {"results": [{**candidate, rating: float("nan")}]},
            "metadata": {"results": [candidate], metadata: {"url": secret_url}},
            "nested": {"results": [nested]},
        }[failure]
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    ("task", "media", "candidate", "expected_attempts"),
    [
        (
            refresh_movie_candidates,
            "movies",
            {"id": 101, "title": "Movie", "vote_average": 8.5},
            4,
        ),
        (
            refresh_game_candidates,
            "games",
            {"id": 201, "name": "Game", "rating": 4.5, "ratings_count": 700},
            6,
        ),
    ],
)
@pytest.mark.parametrize("bad_page", [1, 2])
@pytest.mark.parametrize(
    "failure",
    [
        "json",
        "encoding",
        "root",
        "missing_results",
        "results_type",
        "item_type",
        "missing_id",
        "missing_name",
        "missing_rating",
        "rating_type",
        "nonfinite_rating",
        "metadata",
        "nested",
    ],
)
def test_invalid_candidates_retry_preserves_previous_pool(
    task, media, candidate, expected_attempts, bad_page, failure, monkeypatch, caplog
):
    sentinel = "rawg-candidate-payload-secret"
    secret_url = f"https://api.rawg.io/api/games?key={sentinel}"
    monkeypatch.setattr(settings, "rawg_api_key", sentinel)
    body = _invalid_candidate_body(media, candidate, failure, secret_url)
    requested_pages = []

    def handler(request):
        page = int(request.url.params["page"])
        requested_pages.append(page)
        if media == "games":
            assert request.url.params["key"] == sentinel
        if page == bad_page:
            return httpx.Response(200, content=body)
        return httpx.Response(
            200, json={"results": [candidate], "total_pages": 2, "next": secret_url}
        )

    _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))
    # Exercise the real required-cache-write function; a replacement would also
    # reset the old pool's TTL, so neither value nor expiry may be written.
    previous = (
        {"tmdb_id": 99, "title": "Previous movie", "rating": 8.0}
        if media == "movies"
        else {"rawg_id": 99, "name": "Previous game", "rating": 4.0}
    )
    old_pool = json.dumps([previous]).encode()
    stored = {"value": old_pool, "ttl": 1234}

    class PoolRedis:
        async def set(self, key, value, ex):
            stored.update(value=value, ttl=ex)

    redis = PoolRedis()
    redis.set = AsyncMock(wraps=redis.set)
    monkeypatch.setattr("src.redis.get_redis_client", lambda: redis)
    with caplog.at_level(logging.INFO):
        result = task.apply(throw=False)
    assert result.failed()
    assert isinstance(result.result, InvalidUpstreamPayload)
    assert requested_pages == list(range(1, bad_page + 1)) * expected_attempts
    redis.set.assert_not_awaited()
    assert stored == {"value": old_pool, "ttl": 1234}
    assert "Refreshed" not in caplog.text
    assert sentinel not in caplog.text + str(result.result)


@pytest.mark.parametrize(
    ("task", "candidate", "expected_id"),
    [
        (
            refresh_movie_candidates,
            {"id": 101, "title": "Movie", "vote_average": 8.5},
            "tmdb_id",
        ),
        (
            refresh_game_candidates,
            {"id": 201, "name": "Game", "rating": 4.5, "ratings_count": 700},
            "rawg_id",
        ),
    ],
)
def test_invalid_candidates_can_recover_on_retry(
    task, candidate, expected_id, monkeypatch
):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, content=b"{")
        return httpx.Response(200, json={"results": [candidate]})

    _patch_tasks_client(monkeypatch, httpx.MockTransport(handler))
    write = AsyncMock()
    monkeypatch.setattr("src.recommendations.tasks.cache_set_required", write)
    result = task.apply(throw=False)
    assert result.successful()
    assert calls == 2
    write.assert_awaited_once()
    assert write.call_args.args[1][0][expected_id] == candidate["id"]


async def test_explicit_empty_rawg_results_are_valid(monkeypatch):
    _patch_tasks_client(
        monkeypatch,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []})),
    )
    assert await _fetch_game_candidates() == []
