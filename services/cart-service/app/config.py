from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379"
    JWT_PUBLIC_KEY_PATH: str = "/secrets/jwt_public.pem"
    jwt_public_key: str = ""
    PRODUCT_SERVICE_URL: str = "http://product-service:8001"
    ORDER_SERVICE_URL: str = "http://order-service:8004"
    INVENTORY_SERVICE_URL: str = "http://inventory-service:8005"
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
