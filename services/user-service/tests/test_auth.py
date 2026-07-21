"""
test_auth.py — integration tests for the /auth router.

Uses the fixtures defined in conftest.py:
  - client  : AsyncClient with SQLite + mocked Redis
  - mock_redis : the AsyncMock Redis instance shared with the client
"""
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PASSWORD = "StrongPass1"
TEST_EMAIL = "alice@example.com"


async def _register(client: AsyncClient, email: str = TEST_EMAIL) -> dict:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": VALID_PASSWORD},
    )
    return resp


async def _login(client: AsyncClient, email: str = TEST_EMAIL) -> dict:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": VALID_PASSWORD},
    )
    return resp


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_register_success(self, client: AsyncClient) -> None:
        resp = await _register(client)
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == TEST_EMAIL
        assert body["role"] == "customer"
        assert body["is_active"] is True
        assert body["is_verified"] is False
        assert "id" in body
        assert "created_at" in body

    async def test_register_duplicate_email(self, client: AsyncClient) -> None:
        await _register(client)
        resp = await _register(client)  # second attempt with same email
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

    async def test_register_invalid_email(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": VALID_PASSWORD},
        )
        assert resp.status_code == 422

    async def test_register_weak_password_too_short(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": TEST_EMAIL, "password": "Ab1"},
        )
        assert resp.status_code == 422

    async def test_register_weak_password_no_digit(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": TEST_EMAIL, "password": "NoDigitHere"},
        )
        assert resp.status_code == 422

    async def test_register_weak_password_no_upper(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": TEST_EMAIL, "password": "nouppercase1"},
        )
        assert resp.status_code == 422

    async def test_register_weak_password_no_lower(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/register",
            json={"email": TEST_EMAIL, "password": "NOLOWERCASE1"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_login_success(self, client: AsyncClient, mock_redis: AsyncMock) -> None:
        await _register(client)
        resp = await _login(client)
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        # Redis should have stored the refresh token
        mock_redis.setex.assert_called_once()

    async def test_login_wrong_password(self, client: AsyncClient) -> None:
        await _register(client)
        resp = await client.post(
            "/auth/login",
            json={"email": TEST_EMAIL, "password": "WrongPass1"},
        )
        assert resp.status_code == 401

    async def test_login_unknown_email(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": VALID_PASSWORD},
        )
        assert resp.status_code == 401

    async def test_login_missing_fields(self, client: AsyncClient) -> None:
        resp = await client.post("/auth/login", json={"email": TEST_EMAIL})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    async def test_refresh_success(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        await _register(client)
        login_resp = await _login(client)
        refresh_token = login_resp.json()["refresh_token"]

        # Make Redis return the user-id for this token
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        # We need to simulate the stored user id — re-fetch it from the register response
        register_resp = (await _register(
            client, email="bob@example.com"
        )).json()
        # Use a simpler approach: login as bob and capture the stored user id
        login2 = await _login(client)
        refresh2 = login2.json()["refresh_token"]
        token_hash2 = hashlib.sha256(refresh2.encode()).hexdigest()

        # Re-register alice for a clean state and capture id
        alice_resp = (
            await client.post(
                "/auth/register",
                json={"email": "alice2@example.com", "password": VALID_PASSWORD},
            )
        ).json()
        login3 = await client.post(
            "/auth/login",
            json={"email": "alice2@example.com", "password": VALID_PASSWORD},
        )
        rt = login3.json()["refresh_token"]
        rt_hash = hashlib.sha256(rt.encode()).hexdigest()

        # Configure mock to return the user_id for this specific refresh token
        mock_redis.get = AsyncMock(return_value=alice_resp["id"])

        resp = await client.post("/auth/refresh", json={"refresh_token": rt})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        # The old key should have been deleted (rotated)
        mock_redis.delete.assert_called()

    async def test_refresh_revoked_token(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        # Redis returns None → token not found
        mock_redis.get = AsyncMock(return_value=None)
        await _register(client)
        login_resp = await _login(client)
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert resp.status_code == 401

    async def test_refresh_invalid_token(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/auth/refresh", json={"refresh_token": "completely.invalid.token"}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    async def test_logout_success(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        await _register(client)
        login_resp = await _login(client)
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/auth/logout", json={"refresh_token": refresh_token}
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Successfully logged out."
        # Redis delete should have been called with the hashed key
        mock_redis.delete.assert_called()

    async def test_logout_idempotent(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Logging out an already-revoked token should still return 200."""
        mock_redis.delete = AsyncMock(return_value=0)  # nothing deleted
        resp = await client.post(
            "/auth/logout", json={"refresh_token": "any.token.value"}
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Forgot / Reset password
# ---------------------------------------------------------------------------


class TestPasswordReset:
    async def test_forgot_password_existing_email(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        await _register(client)
        resp = await client.post(
            "/auth/forgot-password", json={"email": TEST_EMAIL}
        )
        assert resp.status_code == 200
        # Redis setex should be called to store the reset token
        mock_redis.setex.assert_called()

    async def test_forgot_password_unknown_email(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        """Must return 200 even for unknown emails to prevent enumeration."""
        mock_redis.setex.reset_mock()
        resp = await client.post(
            "/auth/forgot-password", json={"email": "ghost@example.com"}
        )
        assert resp.status_code == 200
        # Redis must NOT be called for an unknown email
        mock_redis.setex.assert_not_called()

    async def test_reset_password_invalid_token(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get = AsyncMock(return_value=None)
        resp = await client.post(
            "/auth/reset-password",
            json={"token": "invalid-token", "new_password": VALID_PASSWORD},
        )
        assert resp.status_code == 401

    async def test_reset_password_success(
        self, client: AsyncClient, mock_redis: AsyncMock, db_session
    ) -> None:
        # Register a user and grab their id
        reg_resp = await _register(client)
        user_id = reg_resp.json()["id"]

        reset_token = "secure-reset-token-abc123"
        mock_redis.get = AsyncMock(return_value=user_id)

        new_password = "NewSecure99"
        resp = await client.post(
            "/auth/reset-password",
            json={"token": reset_token, "new_password": new_password},
        )
        assert resp.status_code == 200
        mock_redis.delete.assert_called_with(f"pwd_reset:{reset_token}")

        # Old password should no longer work
        login_old = await client.post(
            "/auth/login",
            json={"email": TEST_EMAIL, "password": VALID_PASSWORD},
        )
        assert login_old.status_code == 401

        # New password should work
        mock_redis.setex = AsyncMock(return_value=True)
        login_new = await client.post(
            "/auth/login",
            json={"email": TEST_EMAIL, "password": new_password},
        )
        assert login_new.status_code == 200


# ---------------------------------------------------------------------------
# Public key endpoint
# ---------------------------------------------------------------------------


class TestPublicKey:
    async def test_get_public_key(self, client: AsyncClient) -> None:
        resp = await client.get("/auth/public-key")
        assert resp.status_code == 200
        body = resp.json()
        assert "public_key" in body
        assert body["public_key"].startswith("-----BEGIN PUBLIC KEY-----")

    async def test_public_key_no_auth_required(self, client: AsyncClient) -> None:
        """The public-key endpoint must be accessible without a Bearer token."""
        resp = await client.get("/auth/public-key")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health check (smoke test)
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_health(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "user-service"
