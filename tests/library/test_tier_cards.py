import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, update

from src.auth.models import User
from src.auth.service import create_access_token
from src.dependencies import get_http_client
from src.games.models import UserGame
from src.main import app
from src.movies.models import UserMovie
from src.tierlists.models import TierList


@pytest.mark.parametrize(
    "media,model,ref,catalog,image",
    [
        ("movie", UserMovie, "user_movie_id", "tmdb_id", "catalog_poster_path"),
        ("game", UserGame, "user_game_id", "rawg_id", "catalog_background_image"),
    ],
)
async def test_summary_rename_order_timestamps_and_bounded_sql(
    media, model, ref, catalog, image, client, db, owner, headers
):
    def forbidden():
        pytest.fail("Tier list read requested HTTP")

    app.dependency_overrides[get_http_client] = forbidden
    base = "/api/v1/tierlists"
    created = await client.post(
        base + "/", headers=headers, json={"name": "Original", "media_type": media}
    )
    assert created.status_code == 201
    list_id = created.json()["id"]
    path = f"{base}/{list_id}"
    old_time = datetime(2000, 1, 1, tzinfo=UTC)

    async def age_list():
        await db.execute(
            update(TierList)
            .where(TierList.id == uuid.UUID(list_id))
            .values(updated_at=old_time)
        )
        await db.commit()

    rows = [
        model(
            user_id=owner.id,
            **{catalog: i, image: "/image" if i else None},
            catalog_title=f"Title {i}" if i else None,
            status="planned",
        )
        for i in range(12)
    ]
    db.add_all(rows)
    await db.commit()
    items = []
    for i, row in enumerate(rows):
        await age_list()
        response = await client.post(
            path + "/items",
            headers=headers,
            json={ref: str(row.id), "tier": "S" if i % 2 else "F"},
        )
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["summary"]["tracked_id"] == str(row.id)
        assert item["summary"]["media_type"] == media
        assert item["summary"]["catalog_id"] == i
        assert item["summary"]["catalog_title"] == row.catalog_title
        assert item["summary"][image] == getattr(row, image)
        items.append(item)
    assert (await client.get(path, headers=headers)).json()[
        "updated_at"
    ] != old_time.isoformat()

    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for url in (path, base + "/"):
            queries.clear()
            response = await client.get(url, headers=headers)
            assert response.status_code == 200
            assert (
                len(queries) == 3
            )  # Auth, parent list(s), all items plus both snapshots.
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    before = (await client.get(path, headers=headers)).json()
    assert [(x["tier"], x["position"]) for x in before["items"]] == [
        (tier, p) for tier in ("S", "F") for p in range(6)
    ]
    await age_list()
    renamed = await client.patch(path, headers=headers, json={"name": "  Renamed  "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["items"] == before["items"]
    assert datetime.fromisoformat(renamed.json()["updated_at"]) > old_time

    for body in (
        {},
        {"name": None},
        {"name": ""},
        {"name": " \t\n"},
        {"name": "x" * 101},
    ):
        assert (await client.patch(path, headers=headers, json=body)).status_code == 422
        if "name" in body:
            assert (
                await client.post(
                    base + "/", headers=headers, json={**body, "media_type": media}
                )
            ).status_code == 422
    assert (
        await client.patch(path, headers=headers, json={"name": "x" * 100})
    ).status_code == 200
    other = User(
        email="tier-other@example.com", username="tier-other", hashed_password="x"
    )
    db.add(other)
    await db.commit()
    foreign_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    foreign = await client.patch(path, headers=foreign_headers, json={"name": "Stolen"})
    missing = await client.patch(
        f"{base}/{uuid.uuid4()}", headers=foreign_headers, json={"name": "Stolen"}
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()

    await age_list()
    moved = await client.patch(
        path + f"/items/{items[0]['id']}",
        headers=headers,
        json={"tier": "S", "position": 0},
    )
    assert moved.status_code == 200
    assert moved.json()["summary"] == items[0]["summary"]
    assert (
        datetime.fromisoformat(
            (await client.get(path, headers=headers)).json()["updated_at"]
        )
        > old_time
    )
    await age_list()
    assert (
        await client.delete(path + f"/items/{items[0]['id']}", headers=headers)
    ).status_code == 204
    assert (
        datetime.fromisoformat(
            (await client.get(path, headers=headers)).json()["updated_at"]
        )
        > old_time
    )
    await age_list()
    plural = "movies" if media == "movie" else "games"
    assert (
        await client.delete(f"/api/v1/{plural}/{rows[1].id}", headers=headers)
    ).status_code == 204
    final = (await client.get(path, headers=headers)).json()
    assert datetime.fromisoformat(final["updated_at"]) > old_time
    for tier in ("S", "F"):
        positions = [x["position"] for x in final["items"] if x["tier"] == tier]
        assert positions == list(range(len(positions)))
