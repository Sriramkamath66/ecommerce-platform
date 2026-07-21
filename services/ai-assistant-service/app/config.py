from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379"
    QDRANT_URL: str = "http://localhost:6333"
    VOYAGE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "claude-opus-4-8"
    EMBEDDING_MODEL: str = "voyage-3"
    EMBEDDING_DIMENSION: int = 1024
    JWT_PUBLIC_KEY_PATH: str = "/keys/public_key.pem"
    ORDER_SERVICE_URL: str = "http://order-service:8003"
    PRODUCT_SERVICE_URL: str = "http://product-service:8002"
    INVENTORY_SERVICE_URL: str = "http://inventory-service:8004"
    SESSION_TTL: int = 86400
    MAX_HISTORY_TURNS: int = 20
    QDRANT_PRODUCTS_COLLECTION: str = "products"

    model_config = {"env_file": ".env"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
