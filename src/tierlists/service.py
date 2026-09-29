import logging
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import is_unique_constraint_violation
from src.games.models import UserGame
from src.movies.models import UserMovie
from src.tierlists.constants import MediaType
from src.tierlists.exceptions import (
    ItemAlreadyInTierListException,
    MediaTypeMismatchException,
    TierListItemNotFoundException,
    TierListNotFoundException,
    TierPositionOutOfRangeException,
)
from src.tierlists.models import TierList, TierListItem
from src.tierlists.schemas import (
    AddTierListItemSchema,
    CreateTierListSchema,
    MoveTierListItemSchema,
)

logger = logging.getLogger(__name__)


async def create_tier_list(
    user_id: uuid.UUID,
    data: CreateTierListSchema,
    db: AsyncSession,
) -> TierList:
    tier_list = TierList(user_id=user_id, name=data.name, media_type=data.media_type)
    db.add(tier_list)
    await db.commit()

    stmt = (
        select(TierList)
        .where(TierList.id == tier_list.id)
        .options(selectinload(TierList.items))
    )
    result = await db.execute(stmt)
    created_tier_list = result.scalar_one()

    logger.info("Tier list created: %s for user %s", tier_list.id, user_id)

    return created_tier_list


async def get_tier_list_or_404(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    db: AsyncSession,
) -> TierList:
    stmt = (
        select(TierList)
        .where(TierList.id == tier_list_id, TierList.user_id == user_id)
        .options(selectinload(TierList.items))
    )
    result = await db.execute(stmt)
    tier_list = result.scalar_one_or_none()

    if tier_list is None:
        raise TierListNotFoundException()

    return tier_list


async def get_user_tier_lists(user_id: uuid.UUID, db: AsyncSession) -> list[TierList]:
    stmt = (
        select(TierList)
        .where(TierList.user_id == user_id)
        .options(selectinload(TierList.items))
        .order_by(TierList.created_at.desc(), TierList.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_tier_list(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    tier_list = await get_tier_list_or_404(user_id, tier_list_id, db)
    await db.delete(tier_list)
    await db.commit()

    logger.info("Tier list deleted: %s for user %s", tier_list.id, user_id)


async def _lock_tier_list_or_404(
    user_id: uuid.UUID, tier_list_id: uuid.UUID, db: AsyncSession
) -> TierList:
    # Lock the parent before reading items. All item mutations use this same lock.
    stmt = (
        select(TierList)
        .where(TierList.id == tier_list_id, TierList.user_id == user_id)
        .with_for_update()
    )
    tier_list = (await db.execute(stmt)).scalar_one_or_none()
    if tier_list is None:
        raise TierListNotFoundException()
    return tier_list


async def _list_items(tier_list_id: uuid.UUID, db: AsyncSession) -> list[TierListItem]:
    stmt = (
        select(TierListItem)
        .where(TierListItem.tier_list_id == tier_list_id)
        .order_by(TierListItem.position, TierListItem.id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


def _place_item(
    items: list[TierListItem], item: TierListItem, position: int | None
) -> int:
    index = len(items) if position is None else position
    if index > len(items):
        raise TierPositionOutOfRangeException()
    items.insert(index, item)
    for position, current in enumerate(items):
        current.position = position
    return index


def _is_missing_media_reference(error: IntegrityError) -> bool:
    cause = error.orig
    while cause is not None:
        if getattr(cause, "sqlstate", None) == "23503" and getattr(
            cause, "constraint_name", None
        ) in {
            "tier_list_items_user_movie_id_fkey",
            "tier_list_items_user_game_id_fkey",
        }:
            return True
        cause = cause.__cause__
    return False


async def _assert_item_ownership(
    user_id: uuid.UUID,
    data: AddTierListItemSchema,
    media_type: str,
    db: AsyncSession,
) -> None:
    """
    Verify the referenced tracked movie/game belongs to the user
    and matches the tier list's media_type to protect against user_id spoofing.
    """
    stmt: Select[Any]

    if data.user_movie_id is not None:
        if media_type != MediaType.MOVIE:
            raise MediaTypeMismatchException()
        stmt = (
            select(UserMovie)
            .where(
                UserMovie.id == data.user_movie_id,
                UserMovie.user_id == user_id,
            )
            .with_for_update(read=True, key_share=True)
        )
    else:
        if media_type != MediaType.GAME:
            raise MediaTypeMismatchException()
        stmt = (
            select(UserGame)
            .where(
                UserGame.id == data.user_game_id,
                UserGame.user_id == user_id,
            )
            .with_for_update(read=True, key_share=True)
        )

    result = await db.execute(stmt)

    if result.scalar_one_or_none() is None:
        raise TierListItemNotFoundException()


async def delete_tracked_media_and_compact(
    tracked: UserMovie | UserGame, db: AsyncSession
) -> None:
    """Remove a tracked title and close gaps left by its cascading item deletes.

    Callers lock the tracked row first. Parent list locks are taken in UUID order,
    matching add's media-then-list lock order.
    """
    reference = (
        TierListItem.user_movie_id
        if isinstance(tracked, UserMovie)
        else TierListItem.user_game_id
    )
    affected_ids = list(
        (
            await db.execute(
                select(TierListItem.tier_list_id)
                .where(reference == tracked.id)
                .order_by(TierListItem.tier_list_id)
            )
        )
        .scalars()
        .all()
    )
    for tier_list_id in affected_ids:
        await db.execute(
            select(TierList.id).where(TierList.id == tier_list_id).with_for_update()
        )

    await db.delete(tracked)
    await db.flush()
    for tier_list_id in affected_ids:
        items = await _list_items(tier_list_id, db)
        tiers: dict[str, list[TierListItem]] = {}
        for item in items:
            tiers.setdefault(item.tier, []).append(item)
        for tier_items in tiers.values():
            for position, item in enumerate(tier_items):
                item.position = position
    await db.commit()


async def add_item(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    data: AddTierListItemSchema,
    db: AsyncSession,
) -> TierListItem:
    media_type = await db.scalar(
        select(TierList.media_type).where(
            TierList.id == tier_list_id, TierList.user_id == user_id
        )
    )
    if media_type is None:
        raise TierListNotFoundException()
    await _assert_item_ownership(user_id, data, media_type, db)
    tier_list = await _lock_tier_list_or_404(user_id, tier_list_id, db)

    tier_items = [
        item for item in await _list_items(tier_list.id, db) if item.tier == data.tier
    ]

    item = TierListItem(
        tier_list_id=tier_list.id,
        user_movie_id=data.user_movie_id,
        user_game_id=data.user_game_id,
        tier=data.tier,
        position=0,
    )
    _place_item(tier_items, item, data.position)
    db.add(item)

    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        if any(
            is_unique_constraint_violation(error, constraint)
            for constraint in ("uq_tierlist_movie", "uq_tierlist_game")
        ):
            raise ItemAlreadyInTierListException() from error
        if _is_missing_media_reference(error):
            raise TierListItemNotFoundException() from error
        raise

    await db.refresh(item)

    logger.info(
        "Item %s added to tier list %s for user %s",
        item.id,
        tier_list.id,
        user_id,
    )

    return item


async def move_item(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    data: MoveTierListItemSchema,
    db: AsyncSession,
) -> TierListItem:
    tier_list = await _lock_tier_list_or_404(user_id, tier_list_id, db)
    items = await _list_items(tier_list.id, db)
    item = next((current for current in items if current.id == item_id), None)
    if item is None:
        raise TierListItemNotFoundException()

    source = [current for current in items if current.tier == item.tier]
    source.remove(item)
    target = (
        source
        if item.tier == data.tier
        else [current for current in items if current.tier == data.tier]
    )
    index = len(target) if data.position is None else data.position
    if index > len(target):
        raise TierPositionOutOfRangeException()
    for position, current in enumerate(source):
        current.position = position
    item.tier = data.tier
    _place_item(target, item, index)

    await db.commit()
    await db.refresh(item)

    logger.info(
        "Item %s moved to tier=%s position=%s",
        item.id,
        data.tier,
        index,
    )

    return item


async def delete_item(
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    tier_list = await _lock_tier_list_or_404(user_id, tier_list_id, db)
    items = await _list_items(tier_list.id, db)
    item = next((current for current in items if current.id == item_id), None)
    if item is None:
        raise TierListItemNotFoundException()

    remaining = [
        current for current in items if current.tier == item.tier and current != item
    ]
    for position, current in enumerate(remaining):
        current.position = position
    await db.delete(item)
    await db.commit()

    logger.info("Item %s removed from tier list %s", item.id, tier_list.id)
