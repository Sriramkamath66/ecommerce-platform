"""
conftest.py — test fixtures for user-service.

IMPORTANT: Environment variables and RSA key files must be configured
*before* any app module is imported, because pydantic-settings reads
env vars at class instantiation time (module load).  All setup at the
top of this file runs at collection time, prior to any test-file import.
"""
from __future__ import annotations

import os
import tempfile

# ---------------------------------------------------------------------------
# Step 1 — generate RSA key pair for test JWTs
# ---------------------------------------------------------------------------
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_PRIVATE_PEM: str = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

_PUBLIC_PEM: str = _private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# Write to temp files so the pydantic-settings model_validator can read them
_tmpdir = tempfile.mkdtemp(prefix="user_svc_test_")
_PRIV_PATH = os.path.join(_tmpdir, "private.pem")
_PUB_PATH = os.path.join(_tmpdir, "public.pem")

with open(_PRIV_PATH, "w") as _f:
    _f.write(_PRIVATE_PEM)
with open(_PUB_PATH, "w") as _f:
    _f.write(_PUBLIC_PEM)

# ---------------------------------------------------------------------------
# Step 2 — set env vars BEFORE any app.* import
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["JWT_PRIVATE_KEY_PATH"] = _PRIV_PATH
os.environ["JWT_PUBLIC_KEY_PATH"] = _PUB_PATH
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_FROM", "test@example.com")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

# ---------------------------------------------------------------------------
# Step 3 — safe to import app modules now
# ---------------------------------------------------------------------------
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_redis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


def _make_mock_redis() -> AsyncMock:
    """Return a fully-stubbed async Redis mock."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.ping = AsyncMock(return_value=True)
    mock.aclose = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """SQLite in-memory engine; fresh schema per test function."""
    engine = create_async_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine):
    """Yield a single AsyncSession backed by the in-memory engine."""
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest.fixture(scope="function")
def mock_redis() -> AsyncMock:
    return _make_mock_redis()


@pytest_asyncio.fixture(scope="function")
async def client(test_engine, mock_redis) -> AsyncIterator[AsyncClient]:
    """
    Async HTTP test client with:
      - SQLite in-memory DB via dependency override
      - Mocked Redis via dependency override
      - Lifespan's init_redis / close_redis patched to no-ops so tests
        don't require a real Redis server
    """
    import app.redis_client as rc
    from app.main import create_app

    # Pre-seed the module-level pool so get_redis_client() returns the mock
    # for any code that calls it outside the dependency chain
    rc.redis_pool = mock_redis

    test_app = create_app()

    # Session factory backed by the test SQLite engine
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_get_redis():
        return mock_redis

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_redis] = _override_get_redis

    # Patch init_redis and close_redis in the app.main namespace (where they
    # are imported) so the lifespan doesn't try to open real connections.
    async def _noop_init_redis() -> AsyncMock:
        rc.redis_pool = mock_redis
        return mock_redis

    async def _noop_close_redis() -> None:
        rc.redis_pool = None

    with (
        patch("app.main.init_redis", _noop_init_redis),
        patch("app.main.close_redis", _noop_close_redis),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as ac:
            yield ac

    test_app.dependency_overrides.clear()
    rc.redis_pool = None
