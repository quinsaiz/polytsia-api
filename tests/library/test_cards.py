import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from src.auth.models import User
from src.dependencies import get_http_client
from src.games.models import UserGame
from src.main import app
from src.movies.models import UserMovie

MEDIA = [
    ("movies", UserMovie, "tmdb_id", "title", "poster_path", "release_date"),
    ("games", UserGame, "rawg_id", "name", "background_image", "released"),
]


def payload(media, catalog_id=155):
    if media == "movies":
        return {
            "id": catalog_id,
            "title": "Movie title",
            "overview": "",
            "vote_average": 8.5,
        }
    return {"id": catalog_id, "name": "Game title", "rating": 4.5}


@pytest.mark.parametrize("media,model,id_field,title,image,released", MEDIA)
async def test_tracking_card_reads_and_patch_without_catalog_or_redis(
    media, model, id_field, title, image, released, client, db, headers, monkeypatch
):
    base = f"/api/v1/{media}"
    image_value = "/poster.jpg" if media == "movies" else "https://example.com/game.jpg"
    body = {**payload(media), image: image_value, released: "2008-07-16"}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        response = await client.post(
            f"{base}/track", headers=headers, json={id_field: 155}
        )
    assert response.status_code == 201
    card = response.json()
    assert len(calls) == 1
    assert card["catalog_title"] == body[title]
    assert card[f"catalog_{image}"] == image_value
    assert card["catalog_release_date"] == "2008-07-16"
    assert card["catalog_metadata_fetched_at"] is not None
    record = await db.get(model, uuid.UUID(card["id"]))
    await db.refresh(record)
    assert record.catalog_title == body[title]
    assert str(record.catalog_release_date) == "2008-07-16"

    def forbidden_http():
        pytest.fail("Personal reads must not acquire an HTTP client")

    app.dependency_overrides[get_http_client] = forbidden_http
    for name in ("cache_get", "cache_set"):
        monkeypatch.setattr(
            f"src.{media}.service.{name}",
            AsyncMock(side_effect=AssertionError("Redis used")),
        )
    for path in (f"tracked/{card['id']}", "tracked/by-catalog/155"):
        result = await client.get(f"{base}/{path}", headers=headers)
        assert result.status_code == 200
        assert result.json() == card
        assert (await client.get(f"{base}/{path}")).status_code == 401
    listing = (await client.get(f"{base}/?page_size=1", headers=headers)).json()
    assert listing == {
        "items": [card],
        "total": 1,
        "page": 1,
        "page_size": 1,
        "total_pages": 1,
    }
    updated = await client.patch(
        f"{base}/{card['id']}",
        headers=headers,
        json={
            "notes": "My notes",
            "personal_rating": 9,
            "tier": "S",
            "status": "completed",
            "catalog_title": "Forged",
            f"catalog_{image}": "Forged",
            "catalog_release_date": "2020-01-01",
            "catalog_metadata_fetched_at": None,
        },
    )
    assert updated.status_code == 200
    for field in card:
        if field.startswith("catalog_") or field == "external_rating":
            assert updated.json()[field] == card[field]
    assert updated.json()["notes"] == "My notes"


@pytest.mark.parametrize("media,model,id_field,title,image,released", MEDIA)
@pytest.mark.parametrize(
    "date_value",
    ["omitted", None, "", "invalid", "2025-02-30", "20250101", "2020-01-01T00:00:00"],
)
async def test_optional_snapshot_fields_and_invalid_string_dates(
    media, model, id_field, title, image, released, date_value, client, headers
):
    body = payload(media)
    if date_value != "omitted":
        body[released] = date_value
        body[image] = None
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        response = await client.post(
            f"/api/v1/{media}/track", headers=headers, json={id_field: 155}
        )
    assert response.status_code == 201
    assert response.json()["catalog_title"] == body[title]
    assert response.json()[f"catalog_{image}"] is None
    assert response.json()["catalog_release_date"] is None
    assert response.json()["catalog_metadata_fetched_at"] is not None


@pytest.mark.parametrize("media,model,id_field,title,image,released", MEDIA)
@pytest.mark.parametrize("invalid_field", ["title", "date"])
async def test_invalid_required_title_or_date_type_creates_no_record(
    media, model, id_field, title, image, released, invalid_field, client, headers
):
    body = payload(media)
    body[title if invalid_field == "title" else released] = {"invalid": True}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as upstream:
        app.dependency_overrides[get_http_client] = lambda: upstream
        result = await client.post(
            f"/api/v1/{media}/track", headers=headers, json={id_field: 155}
        )
    assert result.status_code == 503
    assert (await client.get(f"/api/v1/{media}/", headers=headers)).json()["total"] == 0


@pytest.mark.parametrize("media,model,id_field,title,image,released", MEDIA)
async def test_legacy_card_and_private_lookup_isolation(
    media, model, id_field, title, image, released, client, db, owner, headers
):
    other = User(
        email="other-library@example.com",
        username="other-library",
        hashed_password="unused",
    )
    db.add(other)
    await db.flush()
    own = model(
        user_id=owner.id,
        **{id_field: 155},
        status="completed",
        personal_rating=8,
        tier="A",
        notes="Private legacy notes",
        external_rating=4.5,
    )
    # Same catalog ID owned by another user must not make scalar lookup ambiguous.
    shared = model(user_id=other.id, **{id_field: 155}, status="planned")
    foreign = model(user_id=other.id, **{id_field: 156}, status="planned")
    db.add_all([own, shared, foreign])
    await db.commit()
    base = f"/api/v1/{media}"
    cards = [
        (await client.get(f"{base}/{path}", headers=headers)).json()
        for path in (f"tracked/{own.id}", "tracked/by-catalog/155")
    ]
    listing = (await client.get(f"{base}/", headers=headers)).json()
    assert listing["total"] == 1
    cards.append(listing["items"][0])
    for card in cards:
        assert card["id"] == str(own.id)
        assert card["catalog_title"] is None
        assert card[f"catalog_{image}"] is None
        assert card["catalog_release_date"] is None
        assert card["catalog_metadata_fetched_at"] is None
        assert (
            card["personal_rating"],
            card["tier"],
            card["notes"],
            card["external_rating"],
            card["status"],
        ) == (8, "A", "Private legacy notes", 4.5, "completed")
    for path in (
        f"tracked/{foreign.id}",
        f"tracked/{uuid.uuid4()}",
        "tracked/by-catalog/156",
        "tracked/by-catalog/999999",
    ):
        response = await client.get(f"{base}/{path}", headers=headers)
        assert response.status_code == 404
        assert response.json() == {
            "detail": "Movie not found" if media == "movies" else "Game not found"
        }
