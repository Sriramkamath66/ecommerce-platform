from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AppException(Exception):
    """Base class for all service-level exceptions."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_message: str = "An error occurred."

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class UserAlreadyExistsError(AppException):
    status_code = status.HTTP_409_CONFLICT
    default_message = "A user with this email already exists."


class InvalidCredentialsError(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "Invalid email or password."


class InvalidTokenError(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = "The supplied token is invalid or has expired."


class UserNotFoundError(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    default_message = "User not found."


class PermissionDeniedError(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "You do not have permission to perform this action."


class AccountDeactivatedError(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "This account has been deactivated."


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    logger.debug("AppException [%s]: %s", exc.status_code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


async def validation_exception_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
