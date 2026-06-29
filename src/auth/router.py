from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from src.auth.dependencies import CurrentUserDep
from src.auth.schemas import (
    ChangePasswordSchema,
    RefreshTokenRequestSchema,
    TokenSchema,
    UpdateProfileSchema,
    UserRegisterSchema,
    UserResponseSchema,
)
from src.auth.service import (
    change_user_password,
    login_user,
    logout_user,
    refresh_access_token,
    register_user,
    update_user_profile,
)
from src.database import DbSessionDep

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def register(data: UserRegisterSchema, db: DbSessionDep) -> UserResponseSchema:
    return await register_user(data=data, db=db)


@router.post("/login", response_model=TokenSchema)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSessionDep,
) -> TokenSchema:
    return await login_user(
        email=form_data.username,
        password=form_data.password,
        db=db,
    )


@router.get("/me", response_model=UserResponseSchema)
async def get_me(current_user: CurrentUserDep) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current_user)


@router.patch("/me", response_model=UserResponseSchema)
async def update_me(
    current_user: CurrentUserDep,
    data: UpdateProfileSchema,
    db: DbSessionDep,
) -> UserResponseSchema:
    return await update_user_profile(user=current_user, data=data, db=db)


@router.post("/refresh", response_model=TokenSchema)
async def refresh(data: RefreshTokenRequestSchema, db: DbSessionDep) -> TokenSchema:
    return await refresh_access_token(refresh_token=data.refresh_token, db=db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshTokenRequestSchema, db: DbSessionDep) -> None:
    await logout_user(refresh_token=data.refresh_token, db=db)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    current_user: CurrentUserDep,
    data: ChangePasswordSchema,
    db: DbSessionDep,
) -> None:
    await change_user_password(user=current_user, data=data, db=db)
