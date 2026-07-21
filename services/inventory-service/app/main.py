import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import get_settings
from app.database import AsyncSessionLocal, engine
from app.routers import inventory

settings = get_settings()
_listener_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.inventory_service import start_order_event_listener
    global _listener_task
    _listener_task = asyncio.create_task(
        start_order_event_listener(settings.REDIS_URL, AsyncSessionLocal)
    )
    yield
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="Inventory Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)
app.include_router(inventory.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "inventory-service"}
