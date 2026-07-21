import logging
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.games.models import UserGame
from src.movies.models import UserMovie
from src.tierlists.constants import MediaType
from src.tierlists.exceptions import (
    ItemAlreadyInTierListException,
    MediaTypeMismatchException,
    TierListItemNotFoundException,
    TierListNotFoundException,
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
        .order_by(TierList.created_at.desc())
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
        stmt = select(UserMovie).where(
            UserMovie.id == data.user_movie_id,
            UserMovie.user_id == user_id,
        )
    else:
        if media_type != MediaType.GAME:
            raise MediaTypeMismatchException()
        stmt = select(UserGame).where(
            UserGame.id == data.user_game_id,
            UserGame.user_id == user_id,
        )

    result = await db.execute(stmt)

    if result.scalar_one_or_none() is None:
        raise TierListItemNotFoundException()


async def add_item(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    data: AddTierListItemSchema,
    db: AsyncSession,
) -> TierListItem:
    tier_list = await get_tier_list_or_404(user_id, tier_list_id, db)

    await _assert_item_ownership(user_id, data, tier_list.media_type, db)

    item = TierListItem(
        tier_list_id=tier_list.id,
        user_movie_id=data.user_movie_id,
        user_game_id=data.user_game_id,
        tier=data.tier,
        position=data.position,
    )
    db.add(item)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ItemAlreadyInTierListException()

    await db.refresh(item)

    logger.info(
        "Item %s added to tier list %s for user %s",
        item.id,
        tier_list.id,
        user_id,
    )

    return item


async def _get_item_or_404(
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    db: AsyncSession,
) -> TierListItem:
    stmt = select(TierListItem).where(
        TierListItem.id == item_id,
        TierListItem.tier_list_id == tier_list_id,
    )
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()

    if item is None:
        raise TierListItemNotFoundException()

    return item


async def move_item(
    user_id: uuid.UUID,
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    data: MoveTierListItemSchema,
    db: AsyncSession,
) -> TierListItem:
    tier_list = await get_tier_list_or_404(user_id, tier_list_id, db)
    item = await _get_item_or_404(tier_list.id, item_id, db)

    item.tier = data.tier
    item.position = data.position

    db.add(item)
    await db.commit()
    await db.refresh(item)

    logger.info(
        "Item %s moved to tier=%s position=%s",
        item.id,
        data.tier,
        data.position,
    )

    return item


async def delete_item(
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    tier_list = await get_tier_list_or_404(user_id, tier_list_id, db)
    item = await _get_item_or_404(tier_list.id, item_id, db)

    await db.delete(item)
    await db.commit()

    await db.refresh(tier_list, ["items"])

    logger.info("Item %s removed from tier list %s", item.id, tier_list.id)
