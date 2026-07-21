from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import anthropic
import httpx
import voyageai
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from .config import settings
from .routers.chat import router as chat_router
from .services.assistant_service import AssistantService
from .services.rag_service import RAGService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialise and tear down all service dependencies."""
    logger.info("Starting AI Assistant Service — initialising clients…")

    # Redis
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    # Qdrant (async)
    qdrant_client = AsyncQdrantClient(url=settings.QDRANT_URL)

    # Voyage AI (async)
    voyage_client = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)

    # Anthropic (async)
    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # Generic async HTTP client for downstream service calls
    http_client = httpx.AsyncClient()

    # Domain services
    rag_service = RAGService(voyage_client, qdrant_client, settings)
    assistant_service = AssistantService(anthropic_client, rag_service, http_client, settings)

    # Attach everything to app.state so routers can reach them via request.app.state
    app.state.redis = redis_client
    app.state.qdrant = qdrant_client
    app.state.voyage = voyage_client
    app.state.anthropic = anthropic_client
    app.state.http_client = http_client
    app.state.rag_service = rag_service
    app.state.assistant_service = assistant_service
    app.state.settings = settings

    logger.info("All clients initialised — AI Assistant Service ready.")

    yield

    # Graceful shutdown
    logger.info("Shutting down AI Assistant Service — closing clients…")
    await redis_client.aclose()
    await qdrant_client.close()
    await http_client.aclose()
    logger.info("All clients closed.")


app = FastAPI(
    title="AI Assistant Service",
    version="1.0.0",
    description="Streaming AI shopping assistant powered by Claude with RAG and tool use.",
    lifespan=lifespan,
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Chat router
app.include_router(chat_router)


@app.get("/health", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
