"""Abstract base class interface for strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from natras.config.constants import OrderIntent, OrderSide, OrderType, StrategyStatus
from natras.data.candle_engine import Candle


@dataclass
class Signal:
    """Trading signal produced by a strategy."""
    strategy_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    intent: OrderIntent
    quantity: int
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract interface that all NAT RAS strategies must implement."""

    def __init__(self, strategy_id: str, family: str) -> None:
        self.strategy_id = strategy_id
        self.family = family
        self.status = StrategyStatus.AWAITING_SOURCE

    @abstractmethod
    def on_candle(self, candle: Candle, context: Optional[Any] = None) -> List[Signal]:
        """Process completed 5-minute candle and return any generated signals."""
        pass

    def on_tick(self, tick: Any) -> List[Signal]:
        """Process incoming tick. Entries are prohibited from ticks."""
        return []

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status and operational metrics."""
        pass
