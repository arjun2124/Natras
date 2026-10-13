"""Unit tests for StrategyRegistry and BaselineMomentumWickStrategy placeholder."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from natras.config.constants import MARKET_TIMEZONE_STR, STRATEGY_FAMILY
from natras.data.candle_engine import validate_candle
from natras.strategies.base import BaseStrategy, Signal
from natras.strategies.baseline_placeholder import (
    BaselineMomentumWickStrategy,
    StrategyLogicPendingError,
)
from natras.strategies.registry import (
    StrategyRegistry,
    UnauthorizedStrategyFamilyError,
)


def test_baseline_placeholder_refuses_to_invent_rules():
    strategy = BaselineMomentumWickStrategy()
    assert strategy.family == STRATEGY_FAMILY
    assert strategy.status.value == "AWAITING_SOURCE"

    dt = datetime(2026, 10, 14, 9, 30, 0, tzinfo=ZoneInfo(MARKET_TIMEZONE_STR))
    candle = validate_candle("NIFTY", dt, 24500, 24550, 24480, 24520, 1000)

    # Calling on_candle MUST raise StrategyLogicPendingError
    with pytest.raises(StrategyLogicPendingError, match="AWAITING_SOURCE"):
        strategy.on_candle(candle)


def test_baseline_placeholder_status_dict():
    strategy = BaselineMomentumWickStrategy()
    st = strategy.get_status()
    assert st["strategy_id"] == "v2.2.0-baseline"
    assert st["family"] == STRATEGY_FAMILY
    assert st["status"] == "AWAITING_SOURCE"
    assert st["source_verified"] is False
    assert "Awaiting verified source code" in st["message"]


def test_registry_contains_baseline_by_default():
    registry = StrategyRegistry()
    strategies = registry.list_strategies()
    assert len(strategies) == 1
    assert strategies[0]["strategy_id"] == "v2.2.0-RESEARCH-MOMENTUM-WICK"

    retrieved = registry.get("v2.2.0-RESEARCH-MOMENTUM-WICK")
    assert retrieved is not None
    assert retrieved.strategy_id == "v2.2.0-RESEARCH-MOMENTUM-WICK"
    assert retrieved.canonical_name == "Intraday Bar and Wick Momentum"


def test_registry_rejects_unauthorized_strategy_family():
    class RogueStrategy(BaseStrategy):
        def __init__(self):
            super().__init__(strategy_id="rogue_strat", family="crypto-mean-reversion")

        def on_candle(self, candle):
            return []

        def get_status(self):
            return {"strategy_id": self.strategy_id, "family": self.family}

    registry = StrategyRegistry()
    with pytest.raises(UnauthorizedStrategyFamilyError, match="Platform is locked strictly to family"):
        registry.register(RogueStrategy())
