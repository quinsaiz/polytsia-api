import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import hash_password


@pytest.fixture(autouse=True)
def candidate_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[dict[str, object]]]:
    pool: dict[str, list[dict[str, object]]] = {}

    async def fake_cache_get(key: str) -> list[dict[str, object]] | None:
        return pool.get(key)

    monkeypatch.setattr("src.recommendations.service.cache_get", fake_cache_get)
    return pool


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    user = User(
        email="recuser@example.com",
        username="recuser",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user: User) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": test_user.email, "password": "password123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
