import asyncio
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import get_settings
from app.database import AsyncSessionLocal, engine
from app.routers import orders

settings = get_settings()
_listener_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    from app.services.order_service import start_payment_listener
    global _listener_task
    _listener_task = asyncio.create_task(
        start_payment_listener(settings.REDIS_URL, AsyncSessionLocal)
    )
    yield
    # Shutdown
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="Order Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)
app.include_router(orders.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "order-service"}
