import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.movies.models import UserMovie
from src.tierlists import service
from src.tierlists.models import TierList, TierListItem
from src.tierlists.schemas import AddTierListItemSchema
from tests.conftest import TestSessionFactory


async def _committed_list_and_movies(
    count: int,
) -> tuple[User, TierList, list[UserMovie]]:
    suffix = uuid.uuid4().hex
    user = User(
        id=uuid.uuid4(),
        email=f"ordering-{suffix}@example.com",
        username=f"ordering-{suffix[:12]}",
        hashed_password="unused",
    )
    tier_list = TierList(user_id=user.id, name="Ordering", media_type="movie")
    movies = [
        UserMovie(user_id=user.id, tmdb_id=20000 + index, status="watched")
        for index in range(count)
    ]
    async with TestSessionFactory() as db:
        db.add_all([user, tier_list, *movies])
        await db.commit()
    return user, tier_list, movies


async def test_concurrent_adds_use_independent_postgres_sessions() -> None:
    user, tier_list, movies = await _committed_list_and_movies(3)
    async with TestSessionFactory() as db:
        await service.add_item(
            user.id,
            tier_list.id,
            AddTierListItemSchema(user_movie_id=movies[0].id, tier="S"),
            db,
        )

    start = asyncio.Event()

    async def insert(movie: UserMovie) -> None:
        async with TestSessionFactory() as db:
            await start.wait()
            await service.add_item(
                user.id,
                tier_list.id,
                AddTierListItemSchema(user_movie_id=movie.id, tier="S", position=0),
                db,
            )

    tasks = [asyncio.create_task(insert(movie)) for movie in movies[1:]]
    start.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
    async with TestSessionFactory() as db:
        items = (
            (
                await db.execute(
                    select(TierListItem)
                    .where(TierListItem.tier_list_id == tier_list.id)
                    .order_by(TierListItem.position)
                )
            )
            .scalars()
            .all()
        )
    assert [item.position for item in items] == [0, 1, 2]
    assert {item.user_movie_id for item in items} == {movie.id for movie in movies}
    assert items[-1].user_movie_id == movies[0].id


async def test_deleted_movie_after_ownership_check_maps_to_missing_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, tier_list, (movie,) = await _committed_list_and_movies(1)

    async def remove_after_check(
        user_id: uuid.UUID,
        data: AddTierListItemSchema,
        media_type: str,
        db: AsyncSession,
    ) -> None:
        # Simulate a stale ownership read without the production KEY SHARE lock.
        assert await db.scalar(
            select(UserMovie).where(
                UserMovie.id == data.user_movie_id, UserMovie.user_id == user_id
            )
        )
        async with TestSessionFactory() as other:
            stored = await other.get(UserMovie, movie.id)
            assert stored is not None
            await other.delete(stored)
            await other.commit()

    monkeypatch.setattr(service, "_assert_item_ownership", remove_after_check)
    async with TestSessionFactory() as db:
        with pytest.raises(HTTPException) as error:
            await service.add_item(
                user.id,
                tier_list.id,
                AddTierListItemSchema(user_movie_id=movie.id, tier="S"),
                db,
            )
    assert error.value.status_code == 404
    assert error.value.detail == "Tier list item not found"


async def test_duplicate_only_maps_named_media_unique_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, tier_list, (movie,) = await _committed_list_and_movies(1)
    async with TestSessionFactory() as db:
        data = AddTierListItemSchema(user_movie_id=movie.id, tier="S")
        await service.add_item(user.id, tier_list.id, data, db)
        with pytest.raises(HTTPException) as error:
            await service.add_item(user.id, tier_list.id, data, db)
    assert error.value.status_code == 409
    assert error.value.detail == "This item is already in the tier list"

    other_user, other_list, (other_movie,) = await _committed_list_and_movies(1)
    original = service._place_item

    def invalid_position(
        items: list[TierListItem], item: TierListItem, position: int | None
    ) -> int:
        index = original(items, item, position)
        item.position = -1
        return index

    monkeypatch.setattr(service, "_place_item", invalid_position)
    async with TestSessionFactory() as db:
        with pytest.raises(IntegrityError) as other:
            await service.add_item(
                other_user.id,
                other_list.id,
                AddTierListItemSchema(user_movie_id=other_movie.id, tier="S"),
                db,
            )
    cause = other.value.orig
    assert any(
        getattr(candidate, "sqlstate", None) == "23514"
        for candidate in (cause, cause.__cause__)
    )
