from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests (no real Postgres required)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Patches applied for every test module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Override to use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Mock VoyageAI so we never call the real API
FAKE_VECTOR = [0.1] * 1024


@pytest.fixture(autouse=True)
def mock_voyageai():
    with patch("voyageai.Client") as mock_cls:
        instance = MagicMock()
        instance.embed.return_value = MagicMock(embeddings=[FAKE_VECTOR])
        mock_cls.return_value = instance
        yield instance


# Mock Qdrant so we never hit a real Qdrant instance
@pytest.fixture()
def mock_qdrant_service():
    svc = AsyncMock()
    svc.ensure_collection = AsyncMock(return_value=None)
    svc.upsert_product = AsyncMock(return_value=None)
    svc.delete_product = AsyncMock(return_value=None)
    svc.search_products = AsyncMock(return_value=[])
    return svc


@pytest.fixture()
def mock_embedding_service():
    svc = AsyncMock()
    svc.embed_text = AsyncMock(return_value=FAKE_VECTOR)
    svc.embed_product = AsyncMock(return_value=FAKE_VECTOR)
    return svc


@pytest.fixture()
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)   # token not blacklisted
    redis.publish = AsyncMock(return_value=1)
    return redis


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    from app.database import Base

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session
        await session.rollback()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# FastAPI app with overridden dependencies
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def async_client(
    db_session: AsyncSession,
    mock_qdrant_service,
    mock_embedding_service,
    mock_redis,
) -> AsyncGenerator[AsyncClient, None]:
    from app.main import app
    from app import dependencies
    from app.database import get_db

    # Override DB
    async def _get_db_override():
        yield db_session

    # Override services
    async def _get_qdrant():
        return mock_qdrant_service

    async def _get_embedding():
        return mock_embedding_service

    async def _get_redis():
        return mock_redis

    # Override JWT auth — return a fake admin user
    from app.dependencies import CurrentUser

    def _fake_admin():
        return CurrentUser(
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            role="admin",
            email="admin@test.com",
        )

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[dependencies.get_qdrant_service] = _get_qdrant
    app.dependency_overrides[dependencies.get_embedding_service] = _get_embedding
    app.dependency_overrides[dependencies.get_redis] = _get_redis
    app.dependency_overrides[dependencies.get_current_user] = _fake_admin
    app.dependency_overrides[dependencies.require_admin] = _fake_admin
    app.dependency_overrides[dependencies.require_vendor_or_admin] = _fake_admin

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Shared product payload factory
# ---------------------------------------------------------------------------

def make_product_payload(**overrides) -> dict:
    base = {
        "name": "Test Widget",
        "description": "A widget for testing purposes.",
        "price": "19.99",
        "sku": f"SKU-{uuid.uuid4().hex[:8].upper()}",
        "tags": ["test", "widget"],
        "metadata": {"color": "blue"},
        "images": [
            {
                "url": "https://example.com/image.png",
                "alt_text": "Widget image",
                "is_primary": True,
                "sort_order": 0,
            }
        ],
    }
    base.update(overrides)
    return base
