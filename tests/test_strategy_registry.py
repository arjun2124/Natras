"""Unit tests for StrategyRegistry and single family lock."""

import pytest

from natras.config.constants import STRATEGY_FAMILY
from natras.strategies.base import BaseStrategy
from natras.strategies.momentum_wick import MomentumWickStrategy
from natras.strategies.registry import (
    StrategyRegistry,
    UnauthorizedStrategyFamilyError,
)


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
