import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/daily_job_tool"
    test_database_url: str = "postgresql+asyncpg://localhost/daily_job_tool_test"
    user_agent: str = "DailyJobToolBot/0.1 (+https://example.invalid/bot)"
    bot_contact: str = ""  # env BOT_CONTACT: an email or an https URL
    env: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


UA_PRODUCT = "DailyJobToolBot/0.1"
_log = logging.getLogger(__name__)
_warned = False


def user_agent_for(contact: str | None, fallback: str) -> str:
    c = (contact or "").strip()
    if not c:
        return fallback
    return f"{UA_PRODUCT} (+{c})"


def get_user_agent() -> str:
    """UA carrying BOT_CONTACT when set; otherwise the placeholder, with ONE warning per process."""
    global _warned
    s = get_settings()
    if not s.bot_contact.strip():
        if not _warned:
            _warned = True
            _log.warning(
                "BOT_CONTACT is not set: set BOT_CONTACT to a real contact (email or https URL); "
                "some sites block anonymous bots"
            )
        return s.user_agent
    return user_agent_for(s.bot_contact, s.user_agent)
