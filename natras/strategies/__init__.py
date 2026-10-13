"""Strategies package."""

from natras.strategies.base import BaseStrategy, Signal
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
    "MomentumWickStrategy",
    "SteppedTrailingStop",
    "StrategyRegistry",
    "UnauthorizedStrategyFamilyError",
]
