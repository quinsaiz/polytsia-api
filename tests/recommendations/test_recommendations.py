import uuid

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import hash_password
from src.games.models import UserGame
from src.games.schemas import UpdateUserGameSchema
from src.movies.models import UserMovie
from src.movies.schemas import UpdateUserMovieSchema
from src.recommendations.constants import (
    GAME_CANDIDATES_CACHE_KEY,
    MOVIE_CANDIDATES_CACHE_KEY,
)
from src.tierlists.models import TierList, TierListItem


async def _seed_user_movies(user_id: uuid.UUID, db: AsyncSession) -> None:
    db.add_all(
        [
            UserMovie(
                user_id=user_id,
                tmdb_id=1,
                status="completed",
                tier="S",
                personal_rating=9,
                external_rating=8.5,
            ),
            UserMovie(
                user_id=user_id,
                tmdb_id=2,
                status="completed",
                tier="A",
                personal_rating=8,
                external_rating=8.0,
            ),
            UserMovie(
                user_id=user_id,
                tmdb_id=3,
                status="completed",
                tier="C",
                personal_rating=7,
                external_rating=7.0,
            ),
        ]
    )
    await db.commit()


async def _seed_user_games(user_id: uuid.UUID, db: AsyncSession) -> None:
    db.add_all(
        [
            UserGame(
                user_id=user_id,
                rawg_id=10,
                status="completed",
                tier="A",
                personal_rating=9,
                external_rating=4.5,
            ),
            UserGame(
                user_id=user_id,
                rawg_id=11,
                status="completed",
                tier="F",
                personal_rating=8,
                external_rating=4.0,
            ),
            UserGame(
                user_id=user_id,
                rawg_id=12,
                status="completed",
                tier="C",
                personal_rating=7,
                external_rating=3.5,
            ),
        ]
    )
    await db.commit()


class TestRecommendationsAccess:
    async def test_recommendations_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/recommendations/")
        assert response.status_code == 401

    async def test_recommendations_no_data(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["movie_threshold"] is None
        assert data["game_threshold"] is None
        assert data["is_movies_personalized"] is False
        assert data["is_games_personalized"] is False
        assert data["movies_pool_available"] is False
        assert data["games_pool_available"] is False
        assert data["movies"] == []
        assert data["games"] == []

    async def test_movies_endpoint_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/recommendations/movies")
        assert response.status_code == 401

    async def test_games_endpoint_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/recommendations/games")
        assert response.status_code == 401


class TestUserIsolation:
    async def test_other_users_ratings_and_tracked_ids_do_not_affect_results(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        other_user = User(
            email="other-recuser@example.com",
            username="other-recuser",
            hashed_password=hash_password("password123"),
        )
        db.add(other_user)
        await db.flush()
        db.add_all(
            [
                UserMovie(
                    user_id=other_user.id,
                    tmdb_id=tmdb_id,
                    status="completed",
                    tier="S",
                    personal_rating=9,
                    external_rating=8.5,
                )
                for tmdb_id in (100, 101, 102)
            ]
            + [
                UserGame(
                    user_id=other_user.id,
                    rawg_id=rawg_id,
                    status="completed",
                    tier="S",
                    personal_rating=9,
                    external_rating=4.5,
                )
                for rawg_id in (200, 201, 202)
            ]
        )
        await db.commit()
        candidate_pool[MOVIE_CANDIDATES_CACHE_KEY] = [
            {"tmdb_id": 100, "title": "Other User's Movie", "rating": 8.8}
        ]
        candidate_pool[GAME_CANDIDATES_CACHE_KEY] = [
            {"rawg_id": 200, "name": "Other User's Game", "rating": 4.8}
        ]

        own_response = await client.get(
            "/api/v1/recommendations/", headers=auth_headers
        )
        assert own_response.status_code == 200
        own_data = own_response.json()
        assert own_data["movie_threshold"] is None
        assert own_data["game_threshold"] is None
        assert [movie["tmdb_id"] for movie in own_data["movies"]] == [100]
        assert [game["rawg_id"] for game in own_data["games"]] == [200]

        login_response = await client.post(
            "/api/v1/auth/login",
            data={"username": other_user.email, "password": "password123"},
        )
        assert login_response.status_code == 200
        other_headers = {
            "Authorization": f"Bearer {login_response.json()['access_token']}"
        }
        other_response = await client.get(
            "/api/v1/recommendations/", headers=other_headers
        )
        assert other_response.status_code == 200
        other_data = other_response.json()
        assert other_data["movie_threshold"] == pytest.approx(9.0)
        assert other_data["game_threshold"] == pytest.approx(4.5)
        assert other_data["movies"] == []
        assert other_data["games"] == []


async def test_personal_rating_and_tracking_tier_ignore_tier_list_rank(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db: AsyncSession,
    test_user: User,
) -> None:
    await _seed_user_movies(test_user.id, db)
    await _seed_user_games(test_user.id, db)
    movie = (
        await db.execute(select(UserMovie).where(UserMovie.tmdb_id == 1))
    ).scalar_one()
    game = (
        await db.execute(select(UserGame).where(UserGame.rawg_id == 10))
    ).scalar_one()
    movie.external_rating = 1.0
    game.external_rating = 1.0

    lists = [
        TierList(user_id=test_user.id, name=name, media_type=media_type)
        for name, media_type in (
            ("Movies 1", "movie"),
            ("Movies 2", "movie"),
            ("Games 1", "game"),
            ("Games 2", "game"),
        )
    ]
    db.add_all(lists)
    await db.flush()
    db.add_all(
        [
            TierListItem(tier_list_id=lists[0].id, user_movie_id=movie.id, tier="F"),
            TierListItem(tier_list_id=lists[1].id, user_movie_id=movie.id, tier="S"),
            TierListItem(tier_list_id=lists[2].id, user_game_id=game.id, tier="F"),
            TierListItem(tier_list_id=lists[3].id, user_game_id=game.id, tier="S"),
        ]
    )
    await db.commit()

    response = await client.get("/api/v1/recommendations/", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["movie_threshold"] == pytest.approx(91 / 11)
    assert data["game_threshold"] == pytest.approx(27 / 6.5)
    assert data["is_movies_personalized"] is True
    assert data["is_games_personalized"] is True


@pytest.mark.parametrize("schema", [UpdateUserMovieSchema, UpdateUserGameSchema])
def test_personal_rating_uses_ten_point_scale(
    schema: type[UpdateUserMovieSchema] | type[UpdateUserGameSchema],
) -> None:
    for rating in (0, 10):
        assert schema(personal_rating=rating).personal_rating == rating
    for rating in (-1, 11):
        with pytest.raises(ValidationError):
            schema(personal_rating=rating)


@pytest.mark.parametrize("media", ["movies", "games"])
@pytest.mark.parametrize("cleared_field", ["personal_rating", "tier"])
async def test_patch_clear_excludes_rating_from_recommendations(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db: AsyncSession,
    test_user: User,
    candidate_pool: dict[str, list[dict[str, object]]],
    media: str,
    cleared_field: str,
) -> None:
    if media == "movies":
        tracked = [
            UserMovie(
                user_id=test_user.id,
                tmdb_id=index,
                status="completed",
                tier="A",
                personal_rating=8 if index < 4 else 2,
            )
            for index in range(1, 5)
        ]
        cache_key = MOVIE_CANDIDATES_CACHE_KEY
        candidate_pool[cache_key] = [
            {"tmdb_id": 100, "title": "Candidate", "rating": 7.0}
        ]
        threshold_before, threshold_after = 6.5, 8.0
    else:
        tracked = [
            UserGame(
                user_id=test_user.id,
                rawg_id=index,
                status="completed",
                tier="A",
                personal_rating=8 if index < 4 else 2,
            )
            for index in range(1, 5)
        ]
        cache_key = GAME_CANDIDATES_CACHE_KEY
        candidate_pool[cache_key] = [
            {"rawg_id": 100, "name": "Candidate", "rating": 3.3}
        ]
        threshold_before, threshold_after = 3.25, 4.0
    db.add_all(tracked)
    await db.commit()

    recommendations_url = f"/api/v1/recommendations/{media}"
    tracked_url = f"/api/v1/{media}/{tracked[-1].id}"

    before = await client.get(recommendations_url, headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["threshold"] == pytest.approx(threshold_before)
    assert len(before.json()[media]) == 1

    omitted = await client.patch(
        tracked_url, json={"notes": "kept"}, headers=auth_headers
    )
    assert omitted.status_code == 200
    assert omitted.json()["personal_rating"] == 2
    assert omitted.json()["tier"] == "A"
    unchanged = await client.get(recommendations_url, headers=auth_headers)
    assert unchanged.json()["threshold"] == pytest.approx(threshold_before)
    assert len(unchanged.json()[media]) == 1

    cleared = await client.patch(
        tracked_url, json={cleared_field: None}, headers=auth_headers
    )
    assert cleared.status_code == 200
    assert cleared.json()[cleared_field] is None
    other_field = "tier" if cleared_field == "personal_rating" else "personal_rating"
    assert cleared.json()[other_field] == ("A" if other_field == "tier" else 2)
    after = await client.get(recommendations_url, headers=auth_headers)
    assert after.json()["threshold"] == pytest.approx(threshold_after)
    assert after.json()["is_personalized"] is True
    assert after.json()[media] == []


class TestMovieRecommendations:
    async def test_filtered_by_weighted_threshold(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        await _seed_user_movies(user_id=test_user.id, db=db)

        candidate_pool[MOVIE_CANDIDATES_CACHE_KEY] = [
            {
                "tmdb_id": 100,
                "title": "Above Threshold",
                "rating": 8.8,
                "poster_path": None,
            },
            {
                "tmdb_id": 101,
                "title": "Below Threshold",
                "rating": 6.0,
                "poster_path": None,
            },
            {
                "tmdb_id": 1,
                "title": "Already Tracked",
                "rating": 8.5,
                "poster_path": None,
            },
        ]

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["movie_threshold"] == pytest.approx(8.2727, rel=1e-3)
        assert data["is_movies_personalized"] is True

        returned_ids = {m["tmdb_id"] for m in data["movies"]}
        assert 100 in returned_ids
        assert 101 not in returned_ids
        assert 1 not in returned_ids

    async def test_standalone_movies_endpoint(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        await _seed_user_movies(user_id=test_user.id, db=db)
        candidate_pool[MOVIE_CANDIDATES_CACHE_KEY] = [
            {"tmdb_id": 100, "title": "Test", "rating": 8.8, "poster_path": None}
        ]

        response = await client.get(
            "/api/v1/recommendations/movies",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["threshold"] == pytest.approx(8.2727, rel=1e-3)
        assert data["is_personalized"] is True
        assert "games" not in data

    async def test_fewer_than_minimum_falls_back_to_generic(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        db.add_all(
            [
                UserMovie(
                    user_id=test_user.id,
                    tmdb_id=1,
                    status="completed",
                    tier="S",
                    personal_rating=9,
                    external_rating=8.5,
                ),
                UserMovie(
                    user_id=test_user.id,
                    tmdb_id=2,
                    status="completed",
                    tier="C",
                    personal_rating=7,
                    external_rating=7.0,
                ),
            ]
        )
        await db.commit()

        candidate_pool[MOVIE_CANDIDATES_CACHE_KEY] = [
            {
                "tmdb_id": 100,
                "title": "Low Rated But Still Shown",
                "rating": 3.0,
                "poster_path": None,
            }
        ]

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        data = response.json()

        assert data["movie_threshold"] is None
        assert data["is_movies_personalized"] is False
        assert {m["tmdb_id"] for m in data["movies"]} == {100}

    async def test_no_candidates_pool_yet(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
    ) -> None:
        await _seed_user_movies(user_id=test_user.id, db=db)

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        data = response.json()
        assert data["movie_threshold"] is not None
        assert data["movies"] == []
        assert data["movies_pool_available"] is False

    async def test_empty_result_with_existing_pool_is_distinct_from_missing_pool(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        await _seed_user_movies(user_id=test_user.id, db=db)
        candidate_pool[MOVIE_CANDIDATES_CACHE_KEY] = [
            {"tmdb_id": 100, "title": "Below threshold", "rating": 1.0}
        ]

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["movies"] == []
        assert response.json()["movies_pool_available"] is True
        assert response.json()["games_pool_available"] is False

        movie_response = await client.get(
            "/api/v1/recommendations/movies", headers=auth_headers
        )
        game_response = await client.get(
            "/api/v1/recommendations/games", headers=auth_headers
        )
        assert movie_response.json()["pool_available"] is True
        assert game_response.json()["pool_available"] is False

    async def test_ignores_untiered_movies(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
    ) -> None:
        db.add(
            UserMovie(
                user_id=test_user.id,
                tmdb_id=1,
                status="planned",
                tier=None,
                external_rating=8.5,
            )
        )
        await db.commit()

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["movie_threshold"] is None


class TestGameRecommendations:
    async def test_filtered_by_weighted_threshold(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        await _seed_user_games(user_id=test_user.id, db=db)

        candidate_pool[GAME_CANDIDATES_CACHE_KEY] = [
            {
                "rawg_id": 200,
                "name": "Above Threshold",
                "rating": 4.8,
                "background_image": None,
            },
            {
                "rawg_id": 201,
                "name": "Below Threshold",
                "rating": 3.0,
                "background_image": None,
            },
        ]

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["game_threshold"] == pytest.approx(4.1538, rel=1e-3)

        returned_ids = {g["rawg_id"] for g in data["games"]}
        assert 200 in returned_ids
        assert 201 not in returned_ids

    async def test_standalone_games_endpoint(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        await _seed_user_games(user_id=test_user.id, db=db)
        candidate_pool[GAME_CANDIDATES_CACHE_KEY] = [
            {"rawg_id": 200, "name": "Test", "rating": 4.8, "background_image": None}
        ]

        response = await client.get(
            "/api/v1/recommendations/games",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["threshold"] == pytest.approx(4.1538, rel=1e-3)
        assert "movies" not in data

    async def test_fewer_than_minimum_falls_back_to_generic(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
        candidate_pool: dict[str, list[dict[str, object]]],
    ) -> None:
        db.add_all(
            [
                UserGame(
                    user_id=test_user.id,
                    rawg_id=10,
                    status="completed",
                    tier="A",
                    personal_rating=9,
                    external_rating=4.5,
                ),
                UserGame(
                    user_id=test_user.id,
                    rawg_id=11,
                    status="completed",
                    tier="F",
                    personal_rating=8,
                    external_rating=4.0,
                ),
            ]
        )
        await db.commit()

        candidate_pool[GAME_CANDIDATES_CACHE_KEY] = [
            {
                "rawg_id": 200,
                "name": "Low Rated Game",
                "rating": 3.0,
                "background_image": None,
            }
        ]

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        data = response.json()

        assert data["game_threshold"] is None
        assert data["is_games_personalized"] is False
        assert {g["rawg_id"] for g in data["games"]} == {200}

    async def test_no_candidates_pool_yet(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db: AsyncSession,
        test_user: User,
    ) -> None:
        await _seed_user_games(user_id=test_user.id, db=db)

        response = await client.get("/api/v1/recommendations/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["game_threshold"] is not None
        assert data["games"] == []
        assert data["games_pool_available"] is False
