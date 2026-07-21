import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import anthropic
import httpx
import redis.asyncio as aioredis
import voyageai
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from qdrant_client import AsyncQdrantClient

from app.config import settings
from app.routers.recommendations import router as recommendations_router
from app.services.recommendation_service import RecommendationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise all external clients on startup and close them on shutdown."""

    logger.info("Starting Recommendation Service — initialising clients …")

    # Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )

    # Qdrant
    qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)

    # Voyage AI (embeddings)
    voyage_client = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)

    # Anthropic (LLM reranking)
    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Generic HTTP client for inter-service calls
    http_client = httpx.AsyncClient(timeout=10.0)

    # Recommendation service
    recommendation_service = RecommendationService(
        voyage_client=voyage_client,
        qdrant_client=qdrant_client,
        anthropic_client=anthropic_client,
        redis=redis_client,
        http_client=http_client,
        settings=settings,
    )

    # Attach to app state so routers can access them via request.app.state
    app.state.redis = redis_client
    app.state.qdrant = qdrant_client
    app.state.voyage = voyage_client
    app.state.anthropic = anthropic_client
    app.state.http_client = http_client
    app.state.recommendation_service = recommendation_service

    logger.info("All clients initialised — service is ready")

    yield

    # ------------------------------------------------------------------
    # Shutdown: close all clients gracefully
    # ------------------------------------------------------------------
    logger.info("Shutting down Recommendation Service — closing clients …")

    await http_client.aclose()
    await redis_client.aclose()
    await qdrant_client.close()

    logger.info("All clients closed — goodbye")


app = FastAPI(
    title="Recommendation Service",
    version="1.0.0",
    description="Vector-similarity and LLM-powered product recommendations",
    lifespan=lifespan,
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Routers
app.include_router(recommendations_router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Liveness probe used by orchestrators."""
    return {"status": "ok"}
