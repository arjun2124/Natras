"""Baseline placeholder strategy for v2.2.0-RESEARCH-MOMENTUM-WICK.

IMPORTANT:
This strategy strictly refuses to invent, hypothesize, or fabricate trading rules.
It remains inert until the user explicitly provides or confirms the verified source code to port.
"""

from typing import Any, Dict, List

from natras.config.constants import STRATEGY_FAMILY, StrategyStatus
from natras.data.candle_engine import Candle
from natras.strategies.base import BaseStrategy, Signal


class StrategyLogicPendingError(RuntimeError):
    """Raised when an inert placeholder strategy is invoked for trading signals."""
    pass


class BaselineMomentumWickStrategy(BaseStrategy):
    """Inert baseline placeholder for the v2.2.0-RESEARCH-MOMENTUM-WICK strategy family."""

    def __init__(self, strategy_id: str = "v2.2.0-baseline") -> None:
        super().__init__(strategy_id=strategy_id, family=STRATEGY_FAMILY)
        self.status = StrategyStatus.AWAITING_SOURCE
        self.source_verified = False

    def on_candle(self, candle: Candle) -> List[Signal]:
        """Refuse to invent or execute trading rules until exact source is ported."""
        raise StrategyLogicPendingError(
            f"Strategy '{self.strategy_id}' ({self.family}) is in AWAITING_SOURCE status. "
            "Execution is prohibited: refusing to invent or fabricate trading rules. "
            "Please provide or confirm the exact strategy source code to port."
        )

    def get_status(self) -> Dict[str, Any]:
        """Return honest status metadata."""
        return {
            "strategy_id": self.strategy_id,
            "family": self.family,
            "status": self.status.value,
            "source_verified": self.source_verified,
            "message": "Awaiting verified source code port. No speculative rules implemented.",
        }
