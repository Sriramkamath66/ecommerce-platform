from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis, from_url

from app.config import get_settings
from app.database import init_db
from app.dependencies import (
    set_embedding_service,
    set_qdrant_service,
    set_redis,
)
from app.routers import categories, products
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s on port %d …", settings.SERVICE_NAME, settings.PORT)

    # 1. Database tables
    await init_db()

    # 2. Redis connection pool
    redis: Redis = await from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    set_redis(redis)
    logger.info("Redis connected: %s", settings.REDIS_URL)

    # 3. EmbeddingService
    embedding_svc = EmbeddingService(
        api_key=settings.VOYAGE_API_KEY,
        model=settings.EMBEDDING_MODEL,
        dimension=settings.EMBEDDING_DIMENSION,
    )
    set_embedding_service(embedding_svc)

    # 4. QdrantService + ensure collection
    qdrant_svc = QdrantService(
        url=settings.QDRANT_URL,
        collection=settings.QDRANT_COLLECTION,
        dimension=settings.EMBEDDING_DIMENSION,
    )
    set_qdrant_service(qdrant_svc)
    try:
        await qdrant_svc.ensure_collection()
        logger.info(
            "Qdrant collection '%s' ready.", settings.QDRANT_COLLECTION
        )
    except Exception as exc:
        logger.warning("Could not verify Qdrant collection: %s", exc)

    yield

    # Shutdown
    await redis.aclose()
    logger.info("%s shut down.", settings.SERVICE_NAME)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Product Service",
    description="AI-powered product catalogue with semantic search.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# Routers
app.include_router(products.router)
app.include_router(categories.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": settings.SERVICE_NAME,
        "environment": settings.ENVIRONMENT,
    }
