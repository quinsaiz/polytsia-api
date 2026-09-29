import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
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
            json={"tier": "S", "position": 0},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "S"
        assert data["position"] == 0


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


async def _make_movies(db: AsyncSession, user: User, count: int) -> list[UserMovie]:
    movies = [
        UserMovie(user_id=user.id, tmdb_id=10000 + index, status="watched")
        for index in range(count)
    ]
    db.add_all(movies)
    await db.commit()
    return movies


async def _add_movie(
    client: AsyncClient,
    headers: dict[str, str],
    list_id: str,
    movie: UserMovie,
    tier: str,
    position: int | None = None,
) -> str:
    payload: dict[str, str | int] = {"user_movie_id": str(movie.id), "tier": tier}
    if position is not None:
        payload["position"] = position
    response = await client.post(
        f"/api/v1/tierlists/{list_id}/items", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _positions(
    client: AsyncClient, headers: dict[str, str], list_id: str
) -> list[tuple[str, int, str]]:
    response = await client.get(f"/api/v1/tierlists/{list_id}", headers=headers)
    assert response.status_code == 200
    return [
        (item["tier"], item["position"], item["id"])
        for item in response.json()["items"]
    ]


async def test_dense_insert_move_and_delete(
    client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    auth_headers: dict[str, str],
    movie_tier_list_id: str,
) -> None:
    movies = await _make_movies(db, test_user, 4)
    first = await _add_movie(client, auth_headers, movie_tier_list_id, movies[0], "S")
    second = await _add_movie(client, auth_headers, movie_tier_list_id, movies[1], "S")
    third = await _add_movie(
        client, auth_headers, movie_tier_list_id, movies[2], "S", 1
    )
    other = await _add_movie(client, auth_headers, movie_tier_list_id, movies[3], "A")
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, first),
        ("S", 1, third),
        ("S", 2, second),
        ("A", 0, other),
    ]

    async def move(item_id: str, tier: str, position: int | None) -> None:
        payload: dict[str, str | int] = {"tier": tier}
        if position is not None:
            payload["position"] = position
        response = await client.patch(
            f"/api/v1/tierlists/{movie_tier_list_id}/items/{item_id}",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

    await move(first, "S", 2)
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, third),
        ("S", 1, second),
        ("S", 2, first),
        ("A", 0, other),
    ]
    await move(first, "S", 0)
    await move(third, "A", 0)
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, first),
        ("S", 1, second),
        ("A", 0, third),
        ("A", 1, other),
    ]
    await move(third, "S", None)
    await move(second, "S", 1)
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, first),
        ("S", 1, second),
        ("S", 2, third),
        ("A", 0, other),
    ]
    response = await client.delete(
        f"/api/v1/tierlists/{movie_tier_list_id}/items/{second}",
        headers=auth_headers,
    )
    assert response.status_code == 204
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, first),
        ("S", 1, third),
        ("A", 0, other),
    ]


async def test_position_bounds_and_append(
    client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    auth_headers: dict[str, str],
    movie_tier_list_id: str,
) -> None:
    first, second = await _make_movies(db, test_user, 2)
    url = f"/api/v1/tierlists/{movie_tier_list_id}/items"
    for position in (-1, 1):
        response = await client.post(
            url,
            json={"user_movie_id": str(first.id), "tier": "S", "position": position},
            headers=auth_headers,
        )
        assert response.status_code == 422
    first_id = await _add_movie(client, auth_headers, movie_tier_list_id, first, "S")
    second_id = await _add_movie(client, auth_headers, movie_tier_list_id, second, "S")
    for position in (-1, 2):
        response = await client.patch(
            f"{url}/{first_id}",
            json={"tier": "S", "position": position},
            headers=auth_headers,
        )
        assert response.status_code == 422
    response = await client.patch(
        f"{url}/{first_id}", json={"tier": "A", "position": 1}, headers=auth_headers
    )
    assert response.status_code == 422
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, first_id),
        ("S", 1, second_id),
    ]
    response = await client.patch(
        f"{url}/{first_id}", json={"tier": "S"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, second_id),
        ("S", 1, first_id),
    ]


async def test_tracked_movie_deletion_closes_tier_gap_and_keeps_personal_tier(
    client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    auth_headers: dict[str, str],
    movie_tier_list_id: str,
) -> None:
    movies = await _make_movies(db, test_user, 3)
    movies[0].tier = "F"
    await db.commit()
    ids = [
        await _add_movie(client, auth_headers, movie_tier_list_id, movie, "S")
        for movie in movies
    ]
    assert movies[0].tier == "F"
    response = await client.delete(
        f"/api/v1/movies/{movies[1].id}", headers=auth_headers
    )
    assert response.status_code == 204
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("S", 0, ids[0]),
        ("S", 1, ids[2]),
    ]


async def test_tracked_game_deletion_closes_tier_gap(
    client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    auth_headers: dict[str, str],
    game_tier_list_id: str,
) -> None:
    games = [
        UserGame(
            user_id=test_user.id, rawg_id=30000 + index, status="planned", tier="D"
        )
        for index in range(3)
    ]
    db.add_all(games)
    await db.commit()
    ids = []
    for game in games:
        response = await client.post(
            f"/api/v1/tierlists/{game_tier_list_id}/items",
            json={"user_game_id": str(game.id), "tier": "A"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert games[0].tier == "D"
    response = await client.delete(f"/api/v1/games/{games[1].id}", headers=auth_headers)
    assert response.status_code == 204
    assert await _positions(client, auth_headers, game_tier_list_id) == [
        ("A", 0, ids[0]),
        ("A", 1, ids[2]),
    ]


async def test_other_user_cannot_move_or_remove_item(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
    movie_tier_list_id: str,
    tracked_movie: UserMovie,
) -> None:
    item_id = await _add_movie(
        client, auth_headers, movie_tier_list_id, tracked_movie, "C"
    )
    url = f"/api/v1/tierlists/{movie_tier_list_id}/items/{item_id}"
    response = await client.patch(
        url, json={"tier": "S", "position": 0}, headers=other_auth_headers
    )
    assert response.status_code == 404
    response = await client.delete(url, headers=other_auth_headers)
    assert response.status_code == 404
    assert await _positions(client, auth_headers, movie_tier_list_id) == [
        ("C", 0, item_id)
    ]
