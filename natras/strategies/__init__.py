"""Strategies package."""

from natras.strategies.base import BaseStrategy, Signal
from natras.strategies.breadth_trend_flow import BreadthInstitutionalFlowStrategy
from natras.strategies.momentum_wick import (
    MomentumWickStrategy,
    SteppedTrailingStop,
)
from natras.strategies.options_pcr_reversion import OptionsMaxPainPCRReversionStrategy
from natras.strategies.regime_momentum_wick import RegimeAdaptiveMomentumWickStrategy
from natras.strategies.registry import (
    StrategyRegistry,
    UnauthorizedStrategyFamilyError,
)

__all__ = [
    "BaseStrategy",
    "Signal",
    "MomentumWickStrategy",
    "SteppedTrailingStop",
    "RegimeAdaptiveMomentumWickStrategy",
    "OptionsMaxPainPCRReversionStrategy",
    "BreadthInstitutionalFlowStrategy",
    "StrategyRegistry",
    "UnauthorizedStrategyFamilyError",
]
