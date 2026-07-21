from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/inventory_db"
    REDIS_URL: str = "redis://localhost:6379"
    JWT_PUBLIC_KEY_PATH: str = "/secrets/jwt_public.pem"
    jwt_public_key: str = ""
    ENVIRONMENT: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def load_public_key(self) -> "Settings":
        path = Path(self.JWT_PUBLIC_KEY_PATH)
        if path.exists():
            self.jwt_public_key = path.read_text()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
