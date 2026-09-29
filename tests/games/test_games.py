import pytest
from httpx import AsyncClient


class TestSearch:
    async def test_search_games_success(
        self,
        client_with_mock_rawg: AsyncClient,
    ) -> None:
        response = await client_with_mock_rawg.get(
            "/api/v1/games/search",
            params={"query": "Witcher"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "The Witcher 3: Wild Hunt"
        assert data["results"][0]["id"] == 155
        assert data["results"][0]["genres"][0]["name"] == "Action"

    @pytest.mark.parametrize("page", [1, 100])
    async def test_search_page_boundaries(
        self, client_with_mock_rawg: AsyncClient, page: int
    ) -> None:
        response = await client_with_mock_rawg.get(
            "/api/v1/games/search", params={"query": "Witcher", "page": page}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("page", [0, 101])
    async def test_search_page_outside_bounds(
        self, client_with_mock_rawg: AsyncClient, page: int
    ) -> None:
        response = await client_with_mock_rawg.get(
            "/api/v1/games/search", params={"query": "Witcher", "page": page}
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("page_size", [1, 40])
    async def test_search_page_size_boundaries(
        self, client_with_mock_rawg: AsyncClient, page_size: int
    ) -> None:
        response = await client_with_mock_rawg.get(
            "/api/v1/games/search",
            params={"query": "Witcher", "page_size": page_size},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("page_size", [0, 41])
    async def test_search_page_size_outside_bounds(
        self, client_with_mock_rawg: AsyncClient, page_size: int
    ) -> None:
        response = await client_with_mock_rawg.get(
            "/api/v1/games/search",
            params={"query": "Witcher", "page_size": page_size},
        )
        assert response.status_code == 422


class TestGetDetails:
    async def test_get_details_success(
        self,
        client_with_mock_rawg: AsyncClient,
    ) -> None:
        response = await client_with_mock_rawg.get("/api/v1/games/155")
        assert response.status_code == 200
        data = response.json()
        assert (
            data["title"]
            if "title" in data
            else data["name"] == "The Witcher 3: Wild Hunt"
        )
        assert data["id"] == 155

    async def test_get_details_not_found(
        self,
        client_with_mock_rawg: AsyncClient,
    ) -> None:
        response = await client_with_mock_rawg.get("/api/v1/games/999999")
        assert response.status_code == 404


class TestPlatforms:
    async def test_list_platforms_success(
        self,
        client_with_mock_rawg: AsyncClient,
    ) -> None:
        response = await client_with_mock_rawg.get("/api/v1/games/platforms")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert {"id": 1, "name": "PC"} in data["results"]


class TestGenres:
    async def test_list_genres_success(
        self,
        client_with_mock_rawg: AsyncClient,
    ) -> None:
        response = await client_with_mock_rawg.get("/api/v1/games/genres")
        assert response.status_code == 200
        data = response.json()
        assert {"id": 4, "name": "Action"} in data["results"]


class TestTracking:
    async def test_track_game_success(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["rawg_id"] == 155
        assert data["status"] == "planned"
        assert data["external_rating"] == 4.66

    async def test_track_game_duplicate(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        response = await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        assert response.status_code == 409

    async def test_track_game_unauthorized(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
        )
        assert response.status_code == 401

    async def test_list_my_games(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        response = await client_with_mock_rawg.get(
            "/api/v1/games/", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["rawg_id"] == 155

    async def test_update_user_game(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        track_response = await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        user_game_id = track_response.json()["id"]

        response = await client_with_mock_rawg.patch(
            f"/api/v1/games/{user_game_id}",
            json={
                "status": "playing",
                "personal_rating": 10,
                "tier": "S",
                "notes": "Masterpiece RPG",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "playing"
        assert data["personal_rating"] == 10
        assert data["tier"] == "S"
        assert data["notes"] == "Masterpiece RPG"

    async def test_patch_omitted_then_null_optional_fields(
        self, client_with_mock_rawg: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        tracked = await client_with_mock_rawg.post(
            "/api/v1/games/track", json={"rawg_id": 155}, headers=auth_headers
        )
        url = f"/api/v1/games/{tracked.json()['id']}"
        values = {"personal_rating": 8, "tier": "A", "notes": "Good"}
        filled = await client_with_mock_rawg.patch(
            url, json=values, headers=auth_headers
        )
        assert filled.status_code == 200

        omitted = await client_with_mock_rawg.patch(
            url, json={"status": "playing"}, headers=auth_headers
        )
        assert omitted.status_code == 200
        assert omitted.json()["status"] == "playing"
        assert {key: omitted.json()[key] for key in values} == values

        cleared = await client_with_mock_rawg.patch(
            url, json={key: None for key in values}, headers=auth_headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["status"] == "playing"
        assert all(cleared.json()[key] is None for key in values)

    async def test_patch_null_status_is_422(
        self, client_with_mock_rawg: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        tracked = await client_with_mock_rawg.post(
            "/api/v1/games/track", json={"rawg_id": 155}, headers=auth_headers
        )
        response = await client_with_mock_rawg.patch(
            f"/api/v1/games/{tracked.json()['id']}",
            json={"status": None},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("length, expected", [(1000, 200), (1001, 422)])
    async def test_notes_length(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
        length: int,
        expected: int,
    ) -> None:
        tracked = await client_with_mock_rawg.post(
            "/api/v1/games/track", json={"rawg_id": 155}, headers=auth_headers
        )
        response = await client_with_mock_rawg.patch(
            f"/api/v1/games/{tracked.json()['id']}",
            json={"notes": "x" * length},
            headers=auth_headers,
        )
        assert response.status_code == expected

    async def test_delete_user_game(
        self,
        client_with_mock_rawg: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        track_response = await client_with_mock_rawg.post(
            "/api/v1/games/track",
            json={"rawg_id": 155, "status": "planned"},
            headers=auth_headers,
        )
        user_game_id = track_response.json()["id"]

        response = await client_with_mock_rawg.delete(
            f"/api/v1/games/{user_game_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    async def test_update_nonexistent_game(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.patch(
            f"/api/v1/games/{fake_id}",
            json={"status": "playing"},
            headers=auth_headers,
        )
        assert response.status_code == 404
