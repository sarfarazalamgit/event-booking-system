from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Event Booking API"
    environment: str = "development"
    secret_key: SecretStr = Field(
        default=SecretStr("dev-only-change-this-secret-before-deploying"),
        validation_alias="SECRET_KEY",
    )
    access_token_minutes: int = 30

    database_url: str = "postgresql+asyncpg://eventuser:eventpass@localhost:5432/events"
    redis_url: str = "redis://localhost:6379/0"

    resend_api_key: SecretStr | None = None
    email_from: str = "Event Booking <onboarding@resend.dev>"

    db_pool_size: int = 10
    db_max_overflow: int = 20
    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
