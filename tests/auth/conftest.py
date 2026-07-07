import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import hash_password


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    user = User(
        email="test@example.com",
        username="testuser",
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
        data={
            "username": test_user.email,
            "password": "password123",
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
