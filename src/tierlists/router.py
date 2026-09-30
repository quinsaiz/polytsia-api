import uuid

from fastapi import APIRouter, status

from src.auth.dependencies import CurrentUserDep
from src.database import DbSessionDep
from src.tierlists.schemas import (
    AddTierListItemSchema,
    CreateTierListSchema,
    MoveTierListItemSchema,
    RenameTierListSchema,
    TierListItemResponseSchema,
    TierListResponseSchema,
)
from src.tierlists.service import (
    add_item,
    create_tier_list,
    delete_item,
    delete_tier_list,
    get_tier_list_or_404,
    get_user_tier_lists,
    move_item,
    rename_tier_list,
)

router = APIRouter(prefix="/tierlists", tags=["tierlists"])


@router.post(
    "/",
    response_model=TierListResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    data: CreateTierListSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> TierListResponseSchema:
    tier_list = await create_tier_list(user_id=current_user.id, data=data, db=db)
    return TierListResponseSchema.model_validate(tier_list)


@router.get("/", response_model=list[TierListResponseSchema])
async def list_my_tier_lists(
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> list[TierListResponseSchema]:
    tier_lists = await get_user_tier_lists(user_id=current_user.id, db=db)
    return [TierListResponseSchema.model_validate(tl) for tl in tier_lists]


@router.get("/{tier_list_id}", response_model=TierListResponseSchema)
async def get_one(
    tier_list_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> TierListResponseSchema:
    tier_list = await get_tier_list_or_404(
        tier_list_id=tier_list_id,
        user_id=current_user.id,
        db=db,
    )
    return TierListResponseSchema.model_validate(tier_list)


@router.delete("/{tier_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    tier_list_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> None:
    await delete_tier_list(tier_list_id=tier_list_id, user_id=current_user.id, db=db)


@router.post(
    "/{tier_list_id}/items",
    response_model=TierListItemResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def add(
    tier_list_id: uuid.UUID,
    data: AddTierListItemSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> TierListItemResponseSchema:
    item = await add_item(
        tier_list_id=tier_list_id,
        data=data,
        user_id=current_user.id,
        db=db,
    )
    return TierListItemResponseSchema.model_validate(item)


@router.patch(
    "/{tier_list_id}/items/{item_id}",
    response_model=TierListItemResponseSchema,
)
async def move(
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    data: MoveTierListItemSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> TierListItemResponseSchema:
    item = await move_item(
        tier_list_id=tier_list_id,
        item_id=item_id,
        data=data,
        user_id=current_user.id,
        db=db,
    )
    return TierListItemResponseSchema.model_validate(item)


@router.delete(
    "/{tier_list_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove(
    tier_list_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> None:
    await delete_item(
        tier_list_id=tier_list_id,
        item_id=item_id,
        user_id=current_user.id,
        db=db,
    )


@router.patch("/{tier_list_id}", response_model=TierListResponseSchema)
async def rename(
    tier_list_id: uuid.UUID,
    data: RenameTierListSchema,
    current_user: CurrentUserDep,
    db: DbSessionDep,
) -> TierListResponseSchema:
    tier_list = await rename_tier_list(current_user.id, tier_list_id, data, db)
    return TierListResponseSchema.model_validate(tier_list)
