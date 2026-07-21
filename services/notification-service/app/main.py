import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.database import AsyncSessionFactory, engine
from app.routers import notifications
from app.services.email_service import EmailService
from app.services.event_consumer import start_consumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ------------------------------------------------------------------ setup
    logger.info("Notification Service starting up …")

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    http_client = httpx.AsyncClient(timeout=10.0)
    email_service = EmailService(settings)

    app.state.redis = redis_client
    app.state.http_client = http_client
    app.state.email_service = email_service
    app.state.db_session_maker = AsyncSessionFactory

    consumer_task = asyncio.create_task(
        start_consumer(app.state), name="redis-event-consumer"
    )
    logger.info("Redis event consumer started.")

    yield  # application runs here

    # --------------------------------------------------------------- teardown
    logger.info("Notification Service shutting down …")

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await http_client.aclose()
    await redis_client.aclose()
    await engine.dispose()
    logger.info("All connections closed.")


app = FastAPI(
    title="Notification Service",
    version="1.0.0",
    description="Manages in-app and email notifications for the e-commerce platform.",
    lifespan=lifespan,
)

# Prometheus metrics — exposes /metrics
Instrumentator().instrument(app).expose(app)

# Routers
app.include_router(notifications.router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok", "service": "notification-service"}
