import pytest
from httpx import AsyncClient

from src.games.models import UserGame
from src.movies.models import UserMovie


class TestCreateTierList:
    async def test_create_tier_list_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client.post(
            "/api/v1/tierlists/",
            json={"name": "Best RPGs 2024", "media_type": "game"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Best RPGs 2024"
        assert data["media_type"] == "game"
        assert data["items"] == []

    @pytest.mark.parametrize("length, expected", [(100, 201), (101, 422)])
    async def test_name_length(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        length: int,
        expected: int,
    ) -> None:
        response = await client.post(
            "/api/v1/tierlists/",
            json={"name": "x" * length, "media_type": "game"},
            headers=auth_headers,
        )
        assert response.status_code == expected

    async def test_create_tier_list_unauthorized(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/tierlists/",
            json={"name": "Best RPGs 2024", "media_type": "game"},
        )
        assert response.status_code == 401


class TestListTierLists:
    async def test_list_my_tier_lists(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        await client.post(
            "/api/v1/tierlists/",
            json={"name": "Comfort Movies", "media_type": "movie"},
            headers=auth_headers,
        )
        response = await client.get("/api/v1/tierlists/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "Comfort Movies"

    async def test_list_tier_lists_only_own(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        other_auth_headers: dict[str, str],
    ) -> None:
        await client.post(
            "/api/v1/tierlists/",
            json={"name": "My List", "media_type": "movie"},
            headers=auth_headers,
        )
        response = await client.get("/api/v1/tierlists/", headers=other_auth_headers)
        assert response.status_code == 200
        assert response.json() == []


class TestGetTierList:
    async def test_get_tier_list_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
    ) -> None:
        response = await client.get(
            f"/api/v1/tierlists/{movie_tier_list_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["id"] == movie_tier_list_id

    async def test_get_tier_list_not_found(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.get(
            f"/api/v1/tierlists/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_get_other_user_tier_list_forbidden(
        self,
        client: AsyncClient,
        movie_tier_list_id: str,
        other_auth_headers: dict[str, str],
    ) -> None:
        response = await client.get(
            f"/api/v1/tierlists/{movie_tier_list_id}",
            headers=other_auth_headers,
        )
        assert response.status_code == 404


class TestDeleteTierList:
    async def test_delete_tier_list_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
    ) -> None:
        response = await client.delete(
            f"/api/v1/tierlists/{movie_tier_list_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        get_response = await client.get(
            f"/api/v1/tierlists/{movie_tier_list_id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

    async def test_delete_tier_list_not_found(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.delete(
            f"/api/v1/tierlists/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestAddItem:
    async def test_add_movie_item_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        tracked_movie: UserMovie,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={
                "user_movie_id": str(tracked_movie.id),
                "tier": "S",
                "position": 0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user_movie_id"] == str(tracked_movie.id)
        assert data["user_game_id"] is None
        assert data["tier"] == "S"

    async def test_add_game_item_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        game_tier_list_id: str,
        tracked_game: UserGame,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{game_tier_list_id}/items",
            json={
                "user_game_id": str(tracked_game.id),
                "tier": "A",
                "position": 0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user_game_id"] == str(tracked_game.id)
        assert data["user_movie_id"] is None

    async def test_add_item_media_type_mismatch(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        game_tier_list_id: str,
        tracked_movie: UserMovie,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{game_tier_list_id}/items",
            json={"user_movie_id": str(tracked_movie.id), "tier": "S", "position": 0},
            headers=auth_headers,
        )
        assert response.status_code == 400

    async def test_add_item_duplicate(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        tracked_movie: UserMovie,
    ) -> None:
        payload = {
            "user_movie_id": str(tracked_movie.id),
            "tier": "S",
            "position": 0,
        }
        await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json=payload,
            headers=auth_headers,
        )
        response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 409

    async def test_add_item_other_users_movie_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        other_user_tracked_movie: UserMovie,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={
                "user_movie_id": str(other_user_tracked_movie.id),
                "tier": "S",
                "position": 0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_add_item_both_ids_provided_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        tracked_movie: UserMovie,
        tracked_game: UserGame,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={
                "user_movie_id": str(tracked_movie.id),
                "user_game_id": str(tracked_game.id),
                "tier": "S",
                "position": 0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_add_item_no_ids_provided_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
    ) -> None:
        response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={"tier": "S", "position": 0},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestMoveItem:
    async def test_move_item_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        tracked_movie: UserMovie,
    ) -> None:
        item_response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={"user_movie_id": str(tracked_movie.id), "tier": "C", "position": 0},
            headers=auth_headers,
        )
        item_id = item_response.json()["id"]

        response = await client.patch(
            f"/api/v1/tierlists/{movie_tier_list_id}/items/{item_id}",
            json={"tier": "S", "position": 2},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "S"
        assert data["position"] == 2


class TestDeleteItem:
    async def test_delete_item_success(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
        tracked_movie: UserMovie,
    ) -> None:
        item_response = await client.post(
            f"/api/v1/tierlists/{movie_tier_list_id}/items",
            json={"user_movie_id": str(tracked_movie.id), "tier": "C", "position": 0},
            headers=auth_headers,
        )
        item_id = item_response.json()["id"]

        response = await client.delete(
            f"/api/v1/tierlists/{movie_tier_list_id}/items/{item_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        get_response = await client.get(
            f"/api/v1/tierlists/{movie_tier_list_id}",
            headers=auth_headers,
        )
        assert get_response.json()["items"] == []

    async def test_delete_item_not_found(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        movie_tier_list_id: str,
    ) -> None:
        fake_item_id = "00000000-0000-0000-0000-000000000000"

        response = await client.delete(
            f"/api/v1/tierlists/{movie_tier_list_id}/items/{fake_item_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404
