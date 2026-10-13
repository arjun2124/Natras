"""Strategy registry enforcing single strategy family lock."""

from typing import Dict, List, Optional

from natras.config.constants import STRATEGY_FAMILY
from natras.strategies.base import BaseStrategy
from natras.strategies.baseline_placeholder import BaselineMomentumWickStrategy
from natras.strategies.momentum_wick import MomentumWickStrategy


class UnauthorizedStrategyFamilyError(ValueError):
    """Raised when registering a strategy outside the permitted family."""
    pass


class StrategyRegistry:
    """Registry maintaining active and candidate strategies for the locked family."""

    def __init__(self, family: str = STRATEGY_FAMILY) -> None:
        self.permitted_family = family
        self._strategies: Dict[str, BaseStrategy] = {}
        # Register the confirmed active baseline strategy
        self.register(MomentumWickStrategy())

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy instance, verifying it belongs to the permitted family."""
        if strategy.family != self.permitted_family:
            raise UnauthorizedStrategyFamilyError(
                f"Cannot register strategy '{strategy.strategy_id}' belonging to family '{strategy.family}'. "
                f"Platform is locked strictly to family '{self.permitted_family}'."
            )
        self._strategies[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> Optional[BaseStrategy]:
        """Fetch a registered strategy by ID."""
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> List[dict]:
        """List all registered strategies with honest status metadata."""
        return [strat.get_status() for strat in self._strategies.values()]
