import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.auth.models import User
from src.auth.service import create_access_token, hash_password
from src.database import get_db, is_unique_constraint_violation
from src.dependencies import get_http_client
from src.main import app
from tests.conftest import TestSessionFactory


@pytest_asyncio.fixture
async def race_client() -> AsyncGenerator[AsyncClient]:
    async def independent_db() -> AsyncGenerator[AsyncSession]:
        async with TestSessionFactory() as db:
            yield db

    def catalog_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/movie/155"):
            return httpx.Response(
                200,
                json={
                    "id": 155,
                    "title": "Movie",
                    "overview": "A film",
                    "vote_average": 8.5,
                },
            )
        if request.url.path.endswith("/games/155"):
            return httpx.Response(200, json={"id": 155, "name": "Game", "rating": 4.5})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(catalog_response)
    ) as catalog:
        app.dependency_overrides[get_db] = independent_db
        app.dependency_overrides[get_http_client] = lambda: catalog
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.clear()


async def _create_user() -> User:
    suffix = uuid.uuid4().hex
    user = User(
        email=f"race-{suffix}@example.com",
        username=f"race-{suffix[:12]}",
        hashed_password=hash_password("password123"),
    )
    async with TestSessionFactory() as db:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def _race_commits(
    monkeypatch: pytest.MonkeyPatch,
    first: Awaitable[Response],
    second: Awaitable[Response],
) -> tuple[Response, Response]:
    original_commit = AsyncSession.commit
    both_ready = asyncio.Event()
    arrivals = 0

    async def commit_together(self: AsyncSession) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5)
        await original_commit(self)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", commit_together)
        left, right = await asyncio.gather(first, second)
    assert arrivals == 2
    return left, right


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        ("email", "User with this email already exists"),
        ("username", "User with this username already exists"),
    ],
)
async def test_concurrent_registration_duplicate(
    race_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    detail: str,
) -> None:
    suffix = uuid.uuid4().hex
    payloads = [
        {
            "email": f"register-{suffix}-{index}@example.com",
            "username": f"register-{suffix[:12]}-{index}",
            "password": "password123",
            "password_confirm": "password123",
        }
        for index in range(2)
    ]
    payloads[1][field] = payloads[0][field]

    responses = await _race_commits(
        monkeypatch,
        race_client.post("/api/v1/auth/register", json=payloads[0]),
        race_client.post("/api/v1/auth/register", json=payloads[1]),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()[
            "detail"
        ]
        == detail
    )


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        ("email", "User with this email already exists"),
        ("username", "User with this username already exists"),
    ],
)
async def test_concurrent_profile_update_duplicate(
    race_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    detail: str,
) -> None:
    users = [await _create_user(), await _create_user()]
    suffix = uuid.uuid4().hex
    value = (
        f"shared-{suffix}@example.com" if field == "email" else f"shared-{suffix[:12]}"
    )

    responses = await _race_commits(
        monkeypatch,
        *[
            race_client.patch(
                "/api/v1/auth/me",
                json={field: value},
                headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
            )
            for user in users
        ],
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()[
            "detail"
        ]
        == detail
    )


async def test_profile_email_claimed_before_username_autoflush(
    race_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _create_user()
    original_email = user.email
    original_username = user.username
    suffix = uuid.uuid4().hex
    new_email = f"claimed-{suffix}@example.com"
    new_username = f"renamed-{suffix[:12]}"
    email_checked = asyncio.Event()
    claimant_committed = asyncio.Event()
    patch_task: asyncio.Task[Response] | None = None
    username_autoflush_failed = False
    original_execute = AsyncSession.execute

    async def pause_after_email_check(
        self: AsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal username_autoflush_failed
        sql = str(statement)
        if asyncio.current_task() is patch_task and "WHERE users.email =" in sql:
            result = await original_execute(self, statement, *args, **kwargs)
            email_checked.set()
            await asyncio.wait_for(claimant_committed.wait(), timeout=5)
            return result
        if asyncio.current_task() is patch_task and "WHERE users.username =" in sql:
            try:
                return await original_execute(self, statement, *args, **kwargs)
            except IntegrityError:
                username_autoflush_failed = True
                raise
        return await original_execute(self, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", pause_after_email_check)
        patch_task = asyncio.create_task(
            race_client.patch(
                "/api/v1/auth/me",
                json={"email": new_email, "username": new_username},
                headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
            )
        )
        await asyncio.wait_for(email_checked.wait(), timeout=5)
        claimant = await race_client.post(
            "/api/v1/auth/register",
            json={
                "email": new_email,
                "username": f"claimant-{suffix[:12]}",
                "password": "password123",
                "password_confirm": "password123",
            },
        )
        claimant_committed.set()
        response = await patch_task

    assert claimant.status_code == 201
    assert username_autoflush_failed
    assert response.status_code == 409
    assert response.json()["detail"] == "User with this email already exists"
    async with TestSessionFactory() as db:
        stored = await db.scalar(select(User).where(User.id == user.id))
        assert stored is not None
        assert (stored.email, stored.username) == (original_email, original_username)


async def test_profile_updates_email_and_username_together(
    race_client: AsyncClient,
) -> None:
    user = await _create_user()
    suffix = uuid.uuid4().hex
    email = f"updated-{suffix}@example.com"
    username = f"updated-{suffix[:12]}"

    response = await race_client.patch(
        "/api/v1/auth/me",
        json={"email": email, "username": username},
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )

    assert response.status_code == 200
    assert (response.json()["email"], response.json()["username"]) == (
        email,
        username,
    )


@pytest.mark.parametrize(
    ("path", "id_field", "detail"),
    [
        ("movies", "tmdb_id", "Movie is already in your tracking list"),
        ("games", "rawg_id", "Game is already in your tracking list"),
    ],
)
async def test_concurrent_tracking_duplicate(
    race_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    id_field: str,
    detail: str,
) -> None:
    user = await _create_user()
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    url = f"/api/v1/{path}/track"
    body = {id_field: 155}

    responses = await _race_commits(
        monkeypatch,
        race_client.post(url, json=body, headers=headers),
        race_client.post(url, json=body, headers=headers),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()[
            "detail"
        ]
        == detail
    )


@pytest.mark.parametrize(
    ("path", "id_field", "constraint"),
    [
        ("movies", "tmdb_id", "uq_user_movie"),
        ("games", "rawg_id", "uq_user_game"),
    ],
)
async def test_tracking_foreign_key_error_is_not_a_duplicate(
    race_client: AsyncClient, path: str, id_field: str, constraint: str
) -> None:
    missing_user = User(
        id=uuid.uuid4(), email="missing@example.com", username="missing"
    )
    app.dependency_overrides[get_current_user] = lambda: missing_user
    try:
        with pytest.raises(IntegrityError) as error:
            await race_client.post(f"/api/v1/{path}/track", json={id_field: 155})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert not is_unique_constraint_violation(error.value, constraint)
    orig = error.value.orig
    assert orig is not None
    assert any(
        getattr(cause, "sqlstate", None) == "23503" for cause in (orig, orig.__cause__)
    )
