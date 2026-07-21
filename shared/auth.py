"""JWT utilities shared across services. Services verify tokens using the public key
fetched from User Service. Only User Service holds the private key."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

JWT_ALGORITHM = "RS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def create_access_token(
    data: dict[str, Any],
    private_key: str,
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, private_key, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    data: dict[str, Any],
    private_key: str,
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, private_key, algorithm=JWT_ALGORITHM)


def decode_token(token: str, public_key: str) -> dict[str, Any]:
    return jwt.decode(token, public_key, algorithms=[JWT_ALGORITHM])


def verify_access_token(token: str, public_key: str) -> dict[str, Any]:
    try:
        payload = decode_token(token, public_key)
    except ExpiredSignatureError:
        raise ValueError("Token expired")
    except InvalidTokenError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
    if payload.get("type") != "access":
        raise ValueError("Not an access token")
    return payload


def verify_refresh_token(token: str, public_key: str) -> dict[str, Any]:
    try:
        payload = decode_token(token, public_key)
    except ExpiredSignatureError:
        raise ValueError("Token expired")
    except InvalidTokenError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")
    return payload
