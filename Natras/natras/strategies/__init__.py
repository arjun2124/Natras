"""Strategies package."""

from natras.strategies.base import BaseStrategy, Signal
from natras.strategies.baseline_placeholder import (
    BaselineMomentumWickStrategy,
    StrategyLogicPendingError,
)
from natras.strategies.momentum_wick import (
    MomentumWickStrategy,
    SteppedTrailingStop,
)
from natras.strategies.registry import (
    StrategyRegistry,
    UnauthorizedStrategyFamilyError,
)

__all__ = [
    "BaseStrategy",
    "Signal",
    "BaselineMomentumWickStrategy",
    "StrategyLogicPendingError",
    "MomentumWickStrategy",
    "SteppedTrailingStop",
    "StrategyRegistry",
    "UnauthorizedStrategyFamilyError",
]
