import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import hash_password
from src.games.models import UserGame
from src.movies.models import UserMovie


async def create_user_helper(db: AsyncSession, email: str, username: str) -> User:
    user = User(
        email=email,
        username=username,
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_auth_headers_helper(
    client: AsyncClient,
    user_email: str,
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": user_email, "password": "password123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    return await create_user_helper(db, "tieruser@example.com", "tieruser")


@pytest_asyncio.fixture
async def other_user(db: AsyncSession) -> User:
    return await create_user_helper(db, "otheruser@example.com", "otheruser")


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, test_user: User) -> dict[str, str]:
    return await get_auth_headers_helper(client, test_user.email)


@pytest_asyncio.fixture
async def other_auth_headers(client: AsyncClient, other_user: User) -> dict[str, str]:
    return await get_auth_headers_helper(client, other_user.email)


@pytest_asyncio.fixture
async def tracked_movie(db: AsyncSession, test_user: User) -> UserMovie:
    user_movie = UserMovie(user_id=test_user.id, tmdb_id=550, status="watched")
    db.add(user_movie)
    await db.commit()
    await db.refresh(user_movie)
    return user_movie


@pytest_asyncio.fixture
async def another_tracked_movie(db: AsyncSession, test_user: User) -> UserMovie:
    user_movie = UserMovie(user_id=test_user.id, tmdb_id=551, status="watched")
    db.add(user_movie)
    await db.commit()
    await db.refresh(user_movie)
    return user_movie


@pytest_asyncio.fixture
async def tracked_game(db: AsyncSession, test_user: User) -> UserGame:
    user_game = UserGame(user_id=test_user.id, rawg_id=155, status="planned")
    db.add(user_game)
    await db.commit()
    await db.refresh(user_game)
    return user_game


@pytest_asyncio.fixture
async def other_user_tracked_movie(db: AsyncSession, other_user: User) -> UserMovie:
    user_movie = UserMovie(user_id=other_user.id, tmdb_id=552, status="watched")
    db.add(user_movie)
    await db.commit()
    await db.refresh(user_movie)
    return user_movie


@pytest_asyncio.fixture
async def movie_tier_list_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> str:
    response = await client.post(
        "/api/v1/tierlists/",
        json={"name": "Default Movie List", "media_type": "movie"},
        headers=auth_headers,
    )
    return str(response.json()["id"])


@pytest_asyncio.fixture
async def game_tier_list_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> str:
    response = await client.post(
        "/api/v1/tierlists/",
        json={"name": "Default Game List", "media_type": "game"},
        headers=auth_headers,
    )
    return str(response.json()["id"])
