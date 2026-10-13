"""Risk governor enforcing maximum drawdown, position sizing, and exposure limits."""

from dataclasses import dataclass
from typing import List, Tuple

from natras.config.constants import OrderIntent
from natras.config.settings import Settings
from natras.db.models import OrderRecord, PositionRecord


class RiskViolationError(Exception):
    """Raised when an order violates risk limits."""
    pass


@dataclass
class RiskLimits:
    """Configurable risk parameters."""
    max_daily_loss: float = 5000.0
    max_position_size: int = 100
    max_open_positions: int = 2
    max_daily_trades: int = 10


class RiskGovernor:
    """Enforces strict portfolio risk limits prior to order dispatch."""

    def __init__(self, limits: RiskLimits | None = None, settings: Settings | None = None) -> None:
        if settings:
            self.limits = RiskLimits(
                max_daily_loss=settings.max_daily_loss,
                max_position_size=settings.max_position_size,
                max_open_positions=settings.max_open_positions,
                max_daily_trades=settings.max_daily_trades,
            )
        elif limits:
            self.limits = limits
        else:
            self.limits = RiskLimits()

    def evaluate_order(
        self,
        order: OrderRecord,
        open_positions: List[PositionRecord],
        daily_realized_pnl: float,
        daily_trades_count: int,
    ) -> Tuple[bool, str]:
        """Evaluate whether an order passes risk constraints.

        Exit orders are always permitted to allow risk de-escalation.
        Entry orders are strictly evaluated against daily loss, position count, and size limits.
        """
        intent = str(order.intent).upper()
        if intent == OrderIntent.EXIT.value:
            return True, "Exit order approved (risk reduction allowed)."

        # Basic quantity check
        if order.quantity <= 0:
            return False, f"Order quantity ({order.quantity}) must be strictly positive."

        if order.quantity > self.limits.max_position_size:
            return (
                False,
                f"Order quantity ({order.quantity}) exceeds maximum allowed position size "
                f"({self.limits.max_position_size}).",
            )

        # Daily loss circuit breaker
        if daily_realized_pnl <= -abs(self.limits.max_daily_loss):
            return (
                False,
                f"Daily loss limit breached: realized PnL ({daily_realized_pnl:.2f}) "
                f"exceeds max daily loss ({self.limits.max_daily_loss:.2f}). Circuit breaker active.",
            )

        # Maximum concurrent open positions
        if len(open_positions) >= self.limits.max_open_positions:
            return (
                False,
                f"Max open positions reached ({len(open_positions)} >= {self.limits.max_open_positions}).",
            )

        # Maximum daily trade count
        if daily_trades_count >= self.limits.max_daily_trades:
            return (
                False,
                f"Daily trade limit reached ({daily_trades_count} >= {self.limits.max_daily_trades}).",
            )

        return True, "Risk checks passed."

    def validate_or_raise(
        self,
        order: OrderRecord,
        open_positions: List[PositionRecord],
        daily_realized_pnl: float,
        daily_trades_count: int,
    ) -> None:
        """Validate order against risk limits or raise RiskViolationError."""
        passed, reason = self.evaluate_order(
            order=order,
            open_positions=open_positions,
            daily_realized_pnl=daily_realized_pnl,
            daily_trades_count=daily_trades_count,
        )
        if not passed:
            raise RiskViolationError(reason)
