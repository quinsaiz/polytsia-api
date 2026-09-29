import pytest
from httpx import AsyncClient


class TestSearch:
    async def test_search_movies_success(
        self,
        client_with_mock_tmdb: AsyncClient,
    ) -> None:
        response = await client_with_mock_tmdb.get(
            "/api/v1/movies/search",
            params={"query": "Dark Knight"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["title"] == "The Dark Knight"
        assert data["results"][0]["genre_ids"] == [28, 80]

    @pytest.mark.parametrize("page", [1, 500])
    async def test_search_page_boundaries(
        self, client_with_mock_tmdb: AsyncClient, page: int
    ) -> None:
        response = await client_with_mock_tmdb.get(
            "/api/v1/movies/search", params={"query": "Dark Knight", "page": page}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("page", [0, 501])
    async def test_search_page_outside_bounds(
        self, client_with_mock_tmdb: AsyncClient, page: int
    ) -> None:
        response = await client_with_mock_tmdb.get(
            "/api/v1/movies/search", params={"query": "Dark Knight", "page": page}
        )
        assert response.status_code == 422


class TestGetDetails:
    async def test_get_details_success(
        self,
        client_with_mock_tmdb: AsyncClient,
    ) -> None:
        response = await client_with_mock_tmdb.get("/api/v1/movies/155")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "The Dark Knight"
        assert data["genre_ids"] == [28]

    async def test_get_details_not_found(
        self,
        client_with_mock_tmdb: AsyncClient,
    ) -> None:
        response = await client_with_mock_tmdb.get("/api/v1/movies/999999")
        assert response.status_code == 404


class TestGenres:
    async def test_list_genres_success(
        self,
        client_with_mock_tmdb: AsyncClient,
    ) -> None:
        response = await client_with_mock_tmdb.get("/api/v1/movies/genres")
        assert response.status_code == 200
        data = response.json()
        assert {"id": 28, "name": "Action"} in data["genres"]


class TestTracking:
    async def test_track_movie_success(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["tmdb_id"] == 155
        assert data["status"] == "planned"

    async def test_track_movie_duplicate(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        response = await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        assert response.status_code == 409

    async def test_track_movie_unauthorized(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
        )
        assert response.status_code == 401

    async def test_list_my_movies(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        response = await client_with_mock_tmdb.get(
            "/api/v1/movies/", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["tmdb_id"] == 155

    async def test_update_user_movie(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        track_response = await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        user_movie_id = track_response.json()["id"]

        response = await client_with_mock_tmdb.patch(
            f"/api/v1/movies/{user_movie_id}",
            json={"status": "watching", "personal_rating": 9, "tier": "S"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "watching"
        assert data["personal_rating"] == 9
        assert data["tier"] == "S"

    async def test_patch_omitted_then_null_optional_fields(
        self, client_with_mock_tmdb: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        tracked = await client_with_mock_tmdb.post(
            "/api/v1/movies/track", json={"tmdb_id": 155}, headers=auth_headers
        )
        url = f"/api/v1/movies/{tracked.json()['id']}"
        values = {"personal_rating": 8, "tier": "A", "notes": "Good"}
        filled = await client_with_mock_tmdb.patch(
            url, json=values, headers=auth_headers
        )
        assert filled.status_code == 200

        omitted = await client_with_mock_tmdb.patch(
            url, json={"status": "watching"}, headers=auth_headers
        )
        assert omitted.status_code == 200
        assert omitted.json()["status"] == "watching"
        assert {key: omitted.json()[key] for key in values} == values

        cleared = await client_with_mock_tmdb.patch(
            url, json={key: None for key in values}, headers=auth_headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["status"] == "watching"
        assert all(cleared.json()[key] is None for key in values)

    async def test_patch_null_status_is_422(
        self, client_with_mock_tmdb: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        tracked = await client_with_mock_tmdb.post(
            "/api/v1/movies/track", json={"tmdb_id": 155}, headers=auth_headers
        )
        response = await client_with_mock_tmdb.patch(
            f"/api/v1/movies/{tracked.json()['id']}",
            json={"status": None},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("length, expected", [(1000, 200), (1001, 422)])
    async def test_notes_length(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
        length: int,
        expected: int,
    ) -> None:
        tracked = await client_with_mock_tmdb.post(
            "/api/v1/movies/track", json={"tmdb_id": 155}, headers=auth_headers
        )
        response = await client_with_mock_tmdb.patch(
            f"/api/v1/movies/{tracked.json()['id']}",
            json={"notes": "x" * length},
            headers=auth_headers,
        )
        assert response.status_code == expected

    async def test_delete_user_movie(
        self,
        client_with_mock_tmdb: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        track_response = await client_with_mock_tmdb.post(
            "/api/v1/movies/track",
            json={"tmdb_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        user_movie_id = track_response.json()["id"]

        response = await client_with_mock_tmdb.delete(
            f"/api/v1/movies/{user_movie_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    async def test_update_nonexistent_movie(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.patch(
            f"/api/v1/movies/{fake_id}",
            json={"status": "watching"},
            headers=auth_headers,
        )
        assert response.status_code == 404
