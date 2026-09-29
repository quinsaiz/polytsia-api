import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class TokenSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayloadSchema(BaseModel):
    sub: uuid.UUID
    exp: datetime
    type: str


class RefreshTokenRequestSchema(BaseModel):
    refresh_token: str


class UserRegisterSchema(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    password_confirm: str

    @model_validator(mode="after")
    def password_match(self) -> "UserRegisterSchema":
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match")
        return self


class UserResponseSchema(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    username: str
    is_active: bool
    is_verified: bool
    created_at: datetime


class ChangePasswordSchema(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirm: str

    @model_validator(mode="after")
    def password_match(self) -> "ChangePasswordSchema":
        if self.new_password != self.new_password_confirm:
            raise ValueError("Passwords do not match")
        if self.old_password == self.new_password:
            raise ValueError("New password must be different from the old one")
        return self


class UpdateProfileSchema(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(default=None, min_length=3, max_length=50)

    @field_validator("email", "username")
    @classmethod
    def reject_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Explicit null is not allowed")
        return value
