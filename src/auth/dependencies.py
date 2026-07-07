import logging
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from src.auth.constants import ACCESS_TOKEN_TYPE
from src.auth.exceptions import InactiveUserException, InvalidCredentialsException
from src.auth.models import User
from src.auth.service import decode_token
from src.database import DbSessionDep

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSessionDep,
) -> User:
    payload = decode_token(token)

    if payload.type != ACCESS_TOKEN_TYPE:
        raise InvalidCredentialsException()

    stmt = select(User).where(User.id == payload.sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise InvalidCredentialsException()

    if not user.is_active:
        raise InactiveUserException()

    logger.debug("Authenticated user: %s", user.email)
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
