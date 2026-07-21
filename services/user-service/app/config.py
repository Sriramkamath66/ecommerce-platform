from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/users_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT key file paths (values injected via validator below)
    JWT_PRIVATE_KEY_PATH: str = "keys/private.pem"
    JWT_PUBLIC_KEY_PATH: str = "keys/public.pem"

    # Resolved key material — populated by the model_validator, not from env
    jwt_private_key: str = ""
    jwt_public_key: str = ""

    # Token lifetimes
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # SMTP
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_FROM: str = "noreply@example.com"
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # Runtime
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:3000"

    @model_validator(mode="after")
    def read_key_files(self) -> "Settings":
        private_path = Path(self.JWT_PRIVATE_KEY_PATH)
        public_path = Path(self.JWT_PUBLIC_KEY_PATH)

        if private_path.exists():
            self.jwt_private_key = private_path.read_text().strip()

        if public_path.exists():
            self.jwt_public_key = public_path.read_text().strip()

        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
