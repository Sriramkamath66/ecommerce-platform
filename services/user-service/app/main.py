from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.database import Base, engine
from app.exceptions import register_exception_handlers
from app.redis_client import close_redis, init_redis
from app.routers import auth, users

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("user-service starting up (env=%s)", settings.ENVIRONMENT)

    # In development / test, create all tables automatically via SQLAlchemy.
    # In production, rely on Alembic migrations instead.
    if settings.ENVIRONMENT in ("development", "test"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified / created.")

    await init_redis()

    yield

    logger.info("user-service shutting down …")
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="User Service",
        description=(
            "Handles user registration, authentication (RS256 JWT), "
            "profile management, and RBAC for the AI e-commerce platform."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prometheus metrics at /metrics
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app)

    # Domain routers
    app.include_router(auth.router)
    app.include_router(users.router)

    # Exception handlers
    register_exception_handlers(app)

    # Health probe (used by Docker / Kubernetes)
    @app.get("/health", tags=["Health"], include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "service": "user-service"}

    return app


app = create_app()
