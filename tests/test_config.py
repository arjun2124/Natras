"""Unit tests for configuration loading and fail-closed safety."""

import os
from pathlib import Path
import pytest

from natras.config.constants import STRATEGY_FAMILY
from natras.config.settings import (
    ConfigurationError,
    LiveTradingProhibitedError,
    Settings,
    load_settings,
)


def test_default_settings_paper_only():
    settings = Settings()
    assert settings.live_orders_enabled is False
    assert settings.strategy_family == STRATEGY_FAMILY
    assert settings.timezone_name == "Asia/Kolkata"


def test_settings_fails_closed_when_live_orders_true():
    with pytest.raises(LiveTradingProhibitedError, match="FAIL-CLOSED SAFETY TRIGGERED"):
        Settings(live_orders_enabled=True)


def test_settings_fails_on_unauthorized_strategy_family():
    with pytest.raises(ConfigurationError, match="Unauthorized strategy family"):
        Settings(strategy_family="arbitrary-unauthorized-strategy")


def test_settings_repr_hides_secrets():
    settings = Settings(
        fyers_secret_key="my_super_secret_fyers_key",
        telegram_bot_token="my_telegram_token",
    )
    rep = repr(settings)
    assert "my_super_secret_fyers_key" not in rep
    assert "my_telegram_token" not in rep
    assert "credentials='[PROTECTED]'" in rep


def test_load_settings_rejects_live_orders_in_env(monkeypatch):
    monkeypatch.setenv("LIVE_ORDERS_ENABLED", "true")
    with pytest.raises(LiveTradingProhibitedError, match="FAIL-CLOSED"):
        load_settings()

    monkeypatch.setenv("LIVE_ORDERS_ENABLED", "1")
    with pytest.raises(LiveTradingProhibitedError, match="FAIL-CLOSED"):
        load_settings()


def test_load_settings_reads_custom_values(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "LIVE_ORDERS_ENABLED=false\n"
        "SYSTEM_ENV=staging\n"
        "MAX_DAILY_LOSS=7500.0\n"
        "MAX_OPEN_POSITIONS=3\n"
        "FYERS_SECRET_KEY=super_secret_fyers_val\n"
    )
    monkeypatch.delenv("LIVE_ORDERS_ENABLED", raising=False)
    monkeypatch.delenv("SYSTEM_ENV", raising=False)
    monkeypatch.delenv("MAX_DAILY_LOSS", raising=False)
    monkeypatch.delenv("MAX_OPEN_POSITIONS", raising=False)
    monkeypatch.delenv("FYERS_SECRET_KEY", raising=False)

    loaded = load_settings(env_file=env_file)
    assert loaded.live_orders_enabled is False
    assert loaded.system_env == "staging"
    assert loaded.max_daily_loss == 7500.0
    assert loaded.max_open_positions == 3
    assert "super_secret_fyers_val" in loaded.get_secrets()
