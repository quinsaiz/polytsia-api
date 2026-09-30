from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.auth.models import User
from src.auth.service import create_access_token


@pytest_asyncio.fixture
async def owner(db):
    user = User(
        email="library@example.com", username="library", hashed_password="unused"
    )
    db.add(user)
    await db.commit()
    return user


@pytest.fixture
def headers(owner):
    return {"Authorization": f"Bearer {create_access_token(owner.id)}"}


@pytest.fixture(autouse=True)
def no_catalog_cache(monkeypatch):
    for media in ("movies", "games"):
        monkeypatch.setattr(
            f"src.{media}.service.cache_get", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(f"src.{media}.service.cache_set", AsyncMock())
