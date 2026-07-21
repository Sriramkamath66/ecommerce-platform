from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/products_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION: str = "products"

    # VoyageAI
    VOYAGE_API_KEY: str = ""
    EMBEDDING_MODEL: str = "voyage-3"
    EMBEDDING_DIMENSION: int = 1024

    # Inter-service
    USER_SERVICE_URL: str = "http://user-service:8001"

    # JWT — path to RSA public key file; populated via model_validator
    JWT_PUBLIC_KEY_PATH: str = "/run/secrets/jwt_public_key.pem"
    jwt_public_key: str = ""

    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Service identity
    SERVICE_NAME: str = "product-service"
    PORT: int = 8002

    @model_validator(mode="after")
    def _load_jwt_public_key(self) -> "Settings":
        path = Path(self.JWT_PUBLIC_KEY_PATH)
        if path.exists():
            self.jwt_public_key = path.read_text().strip()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
