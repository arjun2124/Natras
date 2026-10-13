"""Unit tests ensuring unfinished modules explicitly fail with NOT_IMPLEMENTED."""

import pytest

from natras.adapters.fyers import FyersAdapter, FyersNotImplementedError
from natras.adapters.telegram import TelegramNotifier, TelegramNotImplementedError
from natras.adapters.upstox import UpstoxAdapter, UpstoxNotImplementedError
from natras.config.constants import ModuleStatus
from natras.research.scaffolding import (
    ResearchNotImplementedError,
    ResearchScaffolding,
)
from natras.shadow.runner import ShadowNotImplementedError, ShadowRunner


def test_fyers_adapter_not_implemented():
    fyers = FyersAdapter()
    assert fyers.status == ModuleStatus.NOT_IMPLEMENTED
    assert fyers.is_implemented() is False
    assert fyers.get_status()["implemented"] is False

    with pytest.raises(FyersNotImplementedError):
        fyers.authenticate()

    with pytest.raises(FyersNotImplementedError):
        fyers.fetch_quotes(["NIFTY"])

    with pytest.raises(FyersNotImplementedError):
        fyers.fetch_candles("NIFTY", "5", "from", "to")

    with pytest.raises(FyersNotImplementedError):
        fyers.subscribe_quotes(["NIFTY"])


def test_upstox_adapter_not_implemented():
    upstox = UpstoxAdapter()
    assert upstox.status == ModuleStatus.NOT_IMPLEMENTED
    assert upstox.is_implemented() is False

    with pytest.raises(UpstoxNotImplementedError):
        upstox.authenticate()

    with pytest.raises(UpstoxNotImplementedError):
        upstox.fetch_historical_candles("NIFTY", "5minute", "to", "from")


def test_telegram_client_not_implemented():
    tg = TelegramNotifier()
    assert tg.status == ModuleStatus.NOT_IMPLEMENTED
    assert tg.is_implemented() is False

    with pytest.raises(TelegramNotImplementedError):
        tg.send_message("Hello from NAT RAS")


def test_research_scaffolding_not_implemented():
    research = ResearchScaffolding()
    assert research.status == ModuleStatus.NOT_IMPLEMENTED
    assert research.is_implemented() is False

    with pytest.raises(ResearchNotImplementedError):
        research.run_backtest()

    with pytest.raises(ResearchNotImplementedError):
        research.evaluate_hypothesis()


def test_shadow_runner_not_implemented():
    shadow = ShadowRunner()
    assert shadow.status == ModuleStatus.NOT_IMPLEMENTED
    assert shadow.is_implemented() is False

    with pytest.raises(ShadowNotImplementedError):
        shadow.register_candidate()

    with pytest.raises(ShadowNotImplementedError):
        shadow.execute_tick()
