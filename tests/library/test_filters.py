import uuid
from datetime import UTC, datetime

import pytest

from src.auth.models import User
from src.dependencies import get_http_client
from src.games.models import UserGame
from src.main import app
from src.movies.models import UserMovie

MEDIA = [("movies", UserMovie, "tmdb_id"), ("games", UserGame, "rawg_id")]


@pytest.mark.parametrize("media,model,catalog_id", MEDIA)
async def test_combined_filters_order_and_private_totals(
    media, model, catalog_id, client, db, owner, headers
):
    def forbidden():
        pytest.fail("Library read requested HTTP")

    app.dependency_overrides[get_http_client] = forbidden
    other = User(email="foreign@example.com", username="foreign", hashed_password="x")
    db.add(other)
    await db.flush()
    rows = []
    for i, (title, rating, tier, status) in enumerate(
        [
            ("Alpha_100%", 8, "A", "completed"),
            ("Alpha_100%", 8, "A", "completed"),
            ("alpha other", 7, "B", "planned"),
            (None, None, None, "planned"),
            ("Zulu", 0, "F", "dropped"),
        ],
        1,
    ):
        row = model(
            id=uuid.UUID(int=i),
            user_id=owner.id,
            **{catalog_id: i},
            catalog_title=title,
            personal_rating=rating,
            tier=tier,
            status=status,
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        rows.append(row)
    db.add_all(
        rows
        + [
            model(
                user_id=other.id,
                **{catalog_id: 1},
                catalog_title="Foreign Alpha_100%",
                personal_rating=8,
                tier="A",
                status="completed",
            )
        ]
    )
    await db.commit()

    async def listing(**params):
        response = await client.get(f"/api/v1/{media}/", headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    for sort in ("created_at", "personal_rating", "catalog_title"):
        for direction in ("asc", "desc"):
            params = dict(
                status="completed",
                tier="A",
                rated="true",
                personal_rating=8,
                query="ALPHA_100%",
                sort_by=sort,
                sort_order=direction,
                page_size=1,
            )
            first = await listing(**params)
            second = await listing(**params, page=2)
            assert first["total"] == second["total"] == 2
            assert first["total_pages"] == 2
            assert [first["items"][0]["id"], second["items"][0]["id"]] == [
                str(r.id) for r in rows[:2]
            ]
            assert (await listing(**params, page=3))["items"] == []
    assert (await listing())["total"] == 5
    assert [x["id"] for x in (await listing())["items"]] == [str(r.id) for r in rows]
    assert (await listing(query="foreign"))["total"] == 0
    assert (await listing(query="%"))["total"] == 2
    assert (await listing(query="_"))["total"] == 2
    assert (await listing(query="alpha"))["total"] == 3
    assert (await listing(rated="false"))["items"][0]["catalog_title"] is None
    assert (await listing(rated="false", personal_rating=8))["total"] == 0
    assert (await listing(personal_rating=0))["items"][0]["id"] == str(rows[4].id)
    for sort in ("personal_rating", "catalog_title"):
        for direction in ("asc", "desc"):
            result = await listing(sort_by=sort, sort_order=direction)
            assert result["items"][-1]["id"] == str(rows[3].id)
    assert [
        x["personal_rating"]
        for x in (await listing(sort_by="personal_rating"))["items"]
    ] == [8, 8, 7, 0, None]


@pytest.mark.parametrize("media,model,catalog_id", MEDIA)
@pytest.mark.parametrize(
    "params",
    [
        {"status": "unknown"},
        {"tier": "E"},
        {"rated": "maybe"},
        {"personal_rating": -1},
        {"personal_rating": 11},
        {"personal_rating": 1.5},
        {"sort_by": "notes"},
        {"sort_order": "DESC"},
        {"query": "x" * 201},
        {"query": ""},
        {"page": 0},
        {"page_size": 101},
    ],
)
async def test_invalid_library_query(media, model, catalog_id, params, client, headers):
    response = await client.get(f"/api/v1/{media}/", headers=headers, params=params)
    assert response.status_code == 422
