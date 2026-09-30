import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.dependencies import get_http_client
from src.main import app


@pytest.mark.parametrize(
    ("media_type", "id_field", "catalog_path", "error_detail"),
    [
        (
            "movies",
            "tmdb_id",
            "/movie/987654321",
            "Movie database service is temporarily unavailable",
        ),
        (
            "games",
            "rawg_id",
            "/games/987654321",
            "Game database service is temporarily unavailable",
        ),
    ],
)
@pytest.mark.parametrize("catalog_status", [429, 500, 503])
async def test_track_catalog_failure_returns_503(
    media_type: str,
    id_field: str,
    catalog_path: str,
    error_detail: str,
    catalog_status: int,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty_cache(_key: str) -> None:
        return None

    monkeypatch.setattr(f"src.{media_type}.service.cache_get", empty_cache)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(catalog_path)
        return httpx.Response(catalog_status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/api/v1/{media_type}/track",
                    json={id_field: 987654321},
                    headers=auth_headers,
                )
                assert response.status_code == 503
                assert response.json() == {"detail": error_detail}

                tracked = await client.get(
                    f"/api/v1/{media_type}/", headers=auth_headers
                )
                assert tracked.status_code == 200
                assert tracked.json()["total"] == 0
        finally:
            app.dependency_overrides.pop(get_http_client, None)


@pytest.mark.parametrize(
    ("media_type", "id_field", "catalog_path", "error_detail"),
    [
        ("movies", "tmdb_id", "/movie/987654321", "Movie not found"),
        ("games", "rawg_id", "/games/987654321", "Game not found"),
    ],
)
async def test_track_catalog_404_remains_not_found(
    media_type: str,
    id_field: str,
    catalog_path: str,
    error_detail: str,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty_cache(_key: str) -> None:
        return None

    monkeypatch.setattr(f"src.{media_type}.service.cache_get", empty_cache)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(catalog_path)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/api/v1/{media_type}/track",
                    json={id_field: 987654321},
                    headers=auth_headers,
                )
                assert response.status_code == 404
                assert response.json() == {"detail": error_detail}
        finally:
            app.dependency_overrides.pop(get_http_client, None)


@pytest.mark.parametrize(
    ("media", "id_field"), [("movies", "tmdb_id"), ("games", "rawg_id")]
)
@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"{}", b'{"id": 987654321}'])
async def test_track_invalid_details_does_not_create_record(
    media, id_field, body, auth_headers, client, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(f"src.{media}.service.cache_get", AsyncMock(return_value=None))
    cache_set = AsyncMock()
    monkeypatch.setattr(f"src.{media}.service.cache_set", cache_set)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    async with httpx.AsyncClient(transport=transport) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as api:
                response = await api.post(
                    f"/api/v1/{media}/track",
                    json={id_field: 987654321},
                    headers=auth_headers,
                )
                assert response.status_code == 503
                label = "Movie" if media == "movies" else "Game"
                assert response.json() == {
                    "detail": f"{label} database service is temporarily unavailable"
                }
                tracked = await api.get(f"/api/v1/{media}/", headers=auth_headers)
                assert tracked.status_code == 200
                assert tracked.json()["total"] == 0
                cache_set.assert_not_awaited()
        finally:
            app.dependency_overrides.pop(get_http_client, None)
