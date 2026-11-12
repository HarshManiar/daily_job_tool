import logging

import pytest

from daily_job_tool import config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(config, "_warned", False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_contact_email_and_url_go_into_ua(monkeypatch):
    monkeypatch.setenv("BOT_CONTACT", "me@example.org")
    config.get_settings.cache_clear()
    assert config.get_user_agent() == "DailyJobToolBot/0.1 (+me@example.org)"
    monkeypatch.setenv("BOT_CONTACT", " https://example.org/bot ")
    config.get_settings.cache_clear()
    assert config.get_user_agent() == "DailyJobToolBot/0.1 (+https://example.org/bot)"


def test_unset_contact_uses_placeholder_and_warns_once(monkeypatch, caplog):
    monkeypatch.delenv("BOT_CONTACT", raising=False)
    monkeypatch.setattr(
        config.Settings, "model_config", {**config.Settings.model_config, "env_file": None}
    )
    config.get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="daily_job_tool.config"):
        a = config.get_user_agent()
        b = config.get_user_agent()
    assert a == b == config.get_settings().user_agent
    msgs = [r for r in caplog.records if "BOT_CONTACT" in r.getMessage()]
    assert len(msgs) == 1 and "set BOT_CONTACT" in msgs[0].getMessage()
