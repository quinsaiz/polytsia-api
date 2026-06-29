import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.constants import ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE
from src.auth.exceptions import (
    EmailAlreadyExistsException,
    InactiveUserException,
    InvalidCredentialsException,
    InvalidPasswordException,
    UsernameAlreadyExistsException,
)
from src.auth.models import RefreshToken, User
from src.auth.schemas import (
    ChangePasswordSchema,
    TokenPayloadSchema,
    TokenSchema,
    UpdateProfileSchema,
    UserRegisterSchema,
    UserResponseSchema,
)
from src.config import settings

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def _create_token(user_id: uuid.UUID, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "exp": now + expires_delta,
        "iat": now,
        "type": token_type,
    }

    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_access_token(user_id: uuid.UUID) -> str:
    return _create_token(
        user_id=user_id,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    return _create_token(
        user_id=user_id,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> TokenPayloadSchema:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return TokenPayloadSchema(
            sub=uuid.UUID(payload["sub"]),
            exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
            type=payload["type"],
        )
    except (JWTError, KeyError, ValueError) as e:
        raise InvalidCredentialsException() from e


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def save_refresh_token(user_id: uuid.UUID, token: str, db: AsyncSession) -> None:
    refresh_token = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(UTC)
        + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(refresh_token)
    await db.commit()


async def refresh_access_token(refresh_token: str, db: AsyncSession) -> TokenSchema:
    payload = decode_token(refresh_token)

    if payload.type != REFRESH_TOKEN_TYPE:
        raise InvalidCredentialsException()

    token_hash = _hash_token(refresh_token)
    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == token_hash,
        RefreshToken.user_id == payload.sub,
    )
    result = await db.execute(stmt)
    db_token = result.scalar_one_or_none()

    if db_token is None:
        raise InvalidCredentialsException()

    if db_token.expires_at < datetime.now(UTC):
        await db.delete(db_token)
        await db.commit()
        raise InvalidCredentialsException()

    await db.delete(db_token)
    await db.commit()

    new_access = create_access_token(payload.sub)
    new_refresh = create_refresh_token(payload.sub)
    await save_refresh_token(payload.sub, new_refresh, db)

    logger.info("Refresh token rotated for user: %s", payload.sub)

    return TokenSchema(
        access_token=new_access,
        refresh_token=new_refresh,
    )


async def logout_user(refresh_token: str, db: AsyncSession) -> None:
    try:
        payload = decode_token(refresh_token)
        user_id = payload.sub
    except InvalidCredentialsException:
        user_id = None

    token_hash = _hash_token(refresh_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    result = await db.execute(stmt)
    db_token = result.scalar_one_or_none()

    if db_token is not None:
        await db.delete(db_token)
        await db.commit()

    logger.info("User logged out: %s, token revoked", user_id)


async def register_user(
    data: UserRegisterSchema,
    db: AsyncSession,
) -> UserResponseSchema:
    stmt = select(User).where(User.email == data.email)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise EmailAlreadyExistsException()

    stmt = select(User).where(User.username == data.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is not None:
        raise UsernameAlreadyExistsException()

    user = User(
        email=data.email,
        username=data.username,
        hashed_password=hash_password(data.password),
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("New user registered: %s", user.email)

    return UserResponseSchema.model_validate(user)


async def login_user(email: str, password: str, db: AsyncSession) -> TokenSchema:
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsException()

    if not user.is_active:
        raise InactiveUserException()

    logger.info("User logged in: %s", user.email)

    new_access = create_access_token(user.id)
    new_refresh = create_refresh_token(user.id)
    await save_refresh_token(user.id, new_refresh, db)

    return TokenSchema(
        access_token=new_access,
        refresh_token=new_refresh,
    )


async def change_user_password(
    user: User,
    data: ChangePasswordSchema,
    db: AsyncSession,
) -> None:
    if not verify_password(data.old_password, user.hashed_password):
        raise InvalidPasswordException()

    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))

    user.hashed_password = hash_password(data.new_password)
    db.add(user)
    await db.commit()

    logger.info("Password changed for user: %s", user.email)


async def update_user_profile(
    user: User,
    data: UpdateProfileSchema,
    db: AsyncSession,
) -> UserResponseSchema:
    if data.email is not None and data.email != user.email:
        stmt = select(User).where(User.email == data.email)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise EmailAlreadyExistsException()

        user.email = data.email
        user.is_verified = False

    if data.username is not None and data.username != user.username:
        stmt = select(User).where(User.username == data.username)
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise UsernameAlreadyExistsException()

        user.username = data.username

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("Profile updated for user: %s", user.id)

    return UserResponseSchema.model_validate(user)
