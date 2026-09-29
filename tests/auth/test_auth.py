import pytest
from httpx import AsyncClient

from src.auth.models import User


class TestRegister:
    async def test_register_success(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "username": "newuser",
                "password": "password123",
                "password_confirm": "password123",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["username"] == "newuser"
        assert "hashed_password" not in data

    async def test_register_duplicate_email(
        self, client: AsyncClient, test_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "username": "anotheruser",
                "password": "password123",
                "password_confirm": "password123",
            },
        )
        assert response.status_code == 409

    async def test_register_password_mismatch(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "another@example.com",
                "username": "another",
                "password": "password123",
                "password_confirm": "different123",
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_success(self, client: AsyncClient, test_user: User) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,
                "password": "password123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(
        self, client: AsyncClient, test_user: User
    ) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,
                "password": "wrongpassword123",
            },
        )
        assert response.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": "nonexistent@example.com",
                "password": "password123",
            },
        )
        assert response.status_code == 401


class TestMe:
    async def test_get_me_success(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ) -> None:
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email

    async def test_get_me_unauthorized(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_patch_omitted_fields_keep_values(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ) -> None:
        response = await client.patch("/api/v1/auth/me", json={}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["email"] == test_user.email
        assert response.json()["username"] == test_user.username

    async def test_patch_one_field_keeps_other(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ) -> None:
        response = await client.patch(
            "/api/v1/auth/me", json={"username": "renameduser"}, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["email"] == test_user.email
        assert response.json()["username"] == "renameduser"

    @pytest.mark.parametrize("field", ["email", "username"])
    async def test_patch_null_required_field_is_422(
        self, client: AsyncClient, auth_headers: dict[str, str], field: str
    ) -> None:
        response = await client.patch(
            "/api/v1/auth/me", json={field: None}, headers=auth_headers
        )
        assert response.status_code == 422


class TestChangePassword:
    async def test_change_password_success(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "old_password": "password123",
                "new_password": "newpassword123",
                "new_password_confirm": "newpassword123",
            },
            headers=auth_headers,
        )
        assert response.status_code == 204

    async def test_change_password_wrong_old(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "old_password": "wrongpassword",
                "new_password": "newpassword123",
                "new_password_confirm": "newpassword123",
            },
            headers=auth_headers,
        )
        assert response.status_code == 400


class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient, test_user: User) -> None:
        login_response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,
                "password": "password123",
            },
        )
        refresh_token = login_response.json()["refresh_token"]

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["refresh_token"] != refresh_token

    async def test_refresh_invalid_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert response.status_code == 401


class TestLogout:
    async def test_logout_success(self, client: AsyncClient, test_user: User) -> None:
        login_response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,
                "password": "password123",
            },
        )
        refresh_token = login_response.json()["refresh_token"]

        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 204

        refresh_response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_response.status_code == 401
