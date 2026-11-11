from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/firsthand"
    test_database_url: str = "postgresql+asyncpg://localhost/firsthand_test"
    user_agent: str = "FirsthandBot/0.1 (+https://example.invalid/bot)"
    env: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
