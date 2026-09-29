from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.recommendations.constants import CANDIDATE_POOL_SIZE, MAX_CANDIDATE_PAGES
from src.recommendations.tasks import (
    _fetch_game_candidates,
    _fetch_movie_candidates,
    refresh_game_candidates,
    refresh_movie_candidates,
)

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
