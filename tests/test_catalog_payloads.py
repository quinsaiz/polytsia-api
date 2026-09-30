"""Exercise the API boundary with real HTTP decoding and schema validation."""

import json
import logging
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from src.config import settings
from src.dependencies import get_http_client
from src.main import app

MOVIE = {"id": 1, "title": "Movie", "overview": "", "vote_average": 8.0}
GAME = {"id": 1, "name": "Game", "rating": 4.0}
ROUTES = [
    (
        "movies",
        "search?query=test",
        {"page": 1, "total_pages": 1, "total_results": 1, "results": [MOVIE]},
        "results",
    ),
    ("movies", "1", MOVIE, "title"),
    ("movies", "genres", {"genres": [{"id": 1, "name": "Drama"}]}, "genres"),
    ("games", "search?query=test", {"count": 1, "results": [GAME]}, "results"),
    ("games", "1", GAME, "name"),
    ("games", "genres", {"results": [{"id": 1, "name": "RPG"}]}, "results"),
    (
        "games",
        "platforms",
        {"count": 1, "results": [{"id": 1, "name": "PC"}]},
        "results",
    ),
]
SENTINEL = "rawg-payload-secret-sentinel"
SECRET_URL = f"https://api.rawg.io/api/games?key={SENTINEL}"


@pytest.fixture
def setup_database() -> None:
    """Public catalog tests require no database."""


def _invalid_catalog_body(media, valid, required, failure):
    payload = deepcopy(valid)
    if failure == "json":
        body = f'{{"url":"{SECRET_URL}",'.encode()
    elif failure == "encoding":
        body = b"\xff"
    elif failure == "root":
        body = json.dumps([SECRET_URL]).encode()
    elif failure == "null":
        body = b"null"
    else:
        if failure == "missing":
            del payload[required]
        elif failure == "field":
            payload[required] = {"url": SECRET_URL}
        elif required in ("results", "genres"):
            payload[required] = [{"url": SECRET_URL}]
        elif media == "movies":
            payload["genres"] = [{"name": SECRET_URL}]  # Missing genre id.
        else:
            payload["platforms"] = [{"platform": {"name": SECRET_URL}}]
        body = json.dumps(payload).encode()
    return body


@pytest.mark.parametrize(("media", "path", "valid", "required"), ROUTES)
@pytest.mark.parametrize(
    "failure", ["json", "encoding", "root", "null", "missing", "field", "nested"]
)
async def test_invalid_catalog_payload_is_503_without_cache_or_secret(
    media, path, valid, required, failure, monkeypatch, caplog
):
    body = _invalid_catalog_body(media, valid, required, failure)
    monkeypatch.setattr(settings, "rawg_api_key", SENTINEL)
    monkeypatch.setattr(f"src.{media}.service.cache_get", AsyncMock(return_value=None))
    cache_set = AsyncMock()
    monkeypatch.setattr(f"src.{media}.service.cache_set", cache_set)

    def handler(request):
        if media == "games":
            assert request.url.params["key"] == SENTINEL
        return httpx.Response(200, content=body)

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as client:
                    response = await client.get(f"/api/v1/{media}/{path}")
            finally:
                app.dependency_overrides.pop(get_http_client, None)
    assert response.status_code == 503
    label = "Movie" if media == "movies" else "Game"
    assert response.json() == {
        "detail": f"{label} database service is temporarily unavailable"
    }
    cache_set.assert_not_awaited()
    assert "Invalid upstream payload" in caplog.text
    assert SENTINEL not in response.text + caplog.text
    assert SECRET_URL not in caplog.text


@pytest.mark.parametrize(("media", "path", "valid", "required"), ROUTES)
@pytest.mark.parametrize("failure", [404, 429, 500, 503, "timeout"])
async def test_existing_catalog_http_error_contract(
    media, path, valid, required, failure, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "rawg_api_key", SENTINEL)
    monkeypatch.setattr(f"src.{media}.service.cache_get", AsyncMock(return_value=None))

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream timeout", request=request)
        return httpx.Response(failure)

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as client:
                    response = await client.get(f"/api/v1/{media}/{path}")
            finally:
                app.dependency_overrides.pop(get_http_client, None)
    expected = 404 if failure == 404 and path == "1" else 503
    assert response.status_code == expected
    assert SENTINEL not in response.text + caplog.text


@pytest.mark.parametrize(
    "genres", [None, {}, [None], ["genre"], [{"name": "Drama"}], [{"id": 1}]]
)
async def test_movie_genre_normalization_rejects_invalid_details(genres, monkeypatch):
    from src.movies.exceptions import TMDBServiceUnavailableException
    from src.movies.service import get_movie_details

    monkeypatch.setattr("src.movies.service.cache_get", AsyncMock(return_value=None))
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={**MOVIE, "genres": genres})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(TMDBServiceUnavailableException):
            await get_movie_details(1, client)


@pytest.mark.parametrize(
    ("media", "rating"), [("movies", "vote_average"), ("games", "rating")]
)
async def test_nonfinite_rating_is_controlled(media, rating, monkeypatch):
    monkeypatch.setattr(f"src.{media}.service.cache_get", AsyncMock(return_value=None))
    payload = {**(MOVIE if media == "movies" else GAME), rating: float("nan")}
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps(payload))
    )
    async with httpx.AsyncClient(transport=transport) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                assert (await client.get(f"/api/v1/{media}/1")).status_code == 503
        finally:
            app.dependency_overrides.pop(get_http_client, None)
