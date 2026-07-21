from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/notifications"
    REDIS_URL: str = "redis://localhost:6379"
    JWT_PUBLIC_KEY_PATH: str = "/keys/public_key.pem"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_FROM: str = "noreply@ecommerce.com"
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    USER_SERVICE_URL: str = "http://user-service:8001"

    model_config = {"env_file": ".env"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
