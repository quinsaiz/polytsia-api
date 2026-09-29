import asyncio
import hashlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import RefreshToken, User
from src.auth.service import hash_password
from src.database import get_db
from src.main import app
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture
async def independent_client() -> AsyncGenerator[tuple[AsyncClient, User]]:
    user = User(
        email=f"rotation-{uuid.uuid4().hex}@example.com",
        username=f"rotation-{uuid.uuid4().hex[:12]}",
        hashed_password=hash_password("password123"),
    )
    async with TestSessionFactory() as db:
        db.add(user)
        await db.commit()
        await db.refresh(user)

    async def independent_db() -> AsyncGenerator[AsyncSession]:
        async with TestSessionFactory() as db:
            yield db

    app.dependency_overrides[get_db] = independent_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, user
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with TestSessionFactory() as db:
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()


async def _login(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": "password123"},
    )
    assert response.status_code == 200
    data: dict[str, str] = response.json()
    return data


async def test_concurrent_refresh_consumes_token_once(
    independent_client: tuple[AsyncClient, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, user = independent_client
    tokens = await _login(client, user)
    selected_twice = asyncio.Event()
    select_count = 0
    original_execute = AsyncSession.execute

    async def synchronize_token_reads(
        self: AsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal select_count
        result = await original_execute(self, statement, *args, **kwargs)
        if "FROM refresh_tokens" in str(statement) and "token_hash" in str(statement):
            select_count += 1
            if select_count == 2:
                selected_twice.set()
            await asyncio.wait_for(selected_twice.wait(), timeout=5)
        return result

    monkeypatch.setattr(AsyncSession, "execute", synchronize_token_reads)
    body = {"refresh_token": tokens["refresh_token"]}
    first, second = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json=body),
        client.post("/api/v1/auth/refresh", json=body),
    )

    assert select_count == 2
    assert sorted([first.status_code, second.status_code]) == [200, 401]
    winner = first if first.status_code == 200 else second
    loser = second if first.status_code == 200 else first
    assert loser.json()["detail"] == "Invalid email or password"
    assert set(winner.json()) == {"access_token", "refresh_token", "token_type"}
    assert winner.json()["token_type"] == "bearer"

    replay = await client.post("/api/v1/auth/refresh", json=body)
    assert replay.status_code == 401
    original_access = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert original_access.status_code == 200


async def test_failed_replacement_rolls_back_old_token(
    independent_client: tuple[AsyncClient, User], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, user = independent_client
    old = await _login(client, user)
    collision = await _login(client, user)
    old_hash = hashlib.sha256(old["refresh_token"].encode()).hexdigest()

    with monkeypatch.context() as patch:
        patch.setattr(
            "src.auth.service.create_refresh_token",
            lambda _user_id: collision["refresh_token"],
        )
        with pytest.raises(IntegrityError):
            await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": old["refresh_token"]},
            )

    async with TestSessionFactory() as db:
        stored_old = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == old_hash)
        )
        assert stored_old is not None

    retry = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old["refresh_token"]},
    )
    assert retry.status_code == 200
    assert retry.json()["refresh_token"] != old["refresh_token"]
