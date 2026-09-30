from unittest.mock import AsyncMock

import pytest

from src.games.models import UserGame
from src.movies.models import UserMovie


@pytest.mark.parametrize(
    "media,model,catalog,title,scale",
    [
        ("movies", UserMovie, "tmdb_id", "title", 1),
        ("games", UserGame, "rawg_id", "name", 2),
    ],
)
@pytest.mark.parametrize("eligible", [0, 2, 3])
@pytest.mark.parametrize(
    "pool_state", ["missing", "empty", "tracked", "below", "boundary"]
)
async def test_recommendation_state_contract(
    media,
    model,
    catalog,
    title,
    scale,
    eligible,
    pool_state,
    client,
    db,
    owner,
    headers,
    monkeypatch,
):
    records = [
        model(
            user_id=owner.id,
            **{catalog: i},
            status="planned",
            personal_rating=8 if i <= eligible else None,
            tier="S",
            external_rating=1,
        )
        for i in range(1, 4)
    ]
    db.add_all(records)
    await db.commit()
    threshold = 8 / scale
    pool = {
        "missing": None,
        "empty": [],
        "tracked": [{catalog: 1, title: "Tracked", "rating": threshold}],
        "below": [{catalog: 99, title: "Below", "rating": threshold - 0.51}],
        "boundary": [{catalog: 99, title: "Boundary", "rating": threshold - 0.5}],
    }[pool_state]
    monkeypatch.setattr(
        "src.recommendations.service.cache_get",
        AsyncMock(side_effect=lambda key: pool if f":{media}:" in key else None),
    )
    response = await client.get(f"/api/v1/recommendations/{media}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    personalized = eligible == 3
    assert body["is_personalized"] is personalized
    assert body["threshold"] == (threshold if personalized else None)
    assert body["pool_available"] is (pool_state != "missing")
    expected = pool_state == "boundary" or (pool_state == "below" and not personalized)
    assert [item[catalog] for item in body[media]] == ([99] if expected else [])
    combined = (await client.get("/api/v1/recommendations/", headers=headers)).json()
    singular = "movie" if media == "movies" else "game"
    assert combined[f"{singular}_threshold"] == body["threshold"]
    assert combined[f"is_{media}_personalized"] == personalized
    assert combined[f"{media}_pool_available"] == body["pool_available"]
    assert combined[media] == body[media]
