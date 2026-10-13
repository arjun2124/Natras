"""Safety guards enforcing fail-closed paper trading and 15:00 IST execution cutoff."""

from datetime import datetime, time
from typing import Tuple
from zoneinfo import ZoneInfo

from natras.config.constants import (
    ENTRY_CUTOFF_HOUR,
    ENTRY_CUTOFF_MINUTE,
    MARKET_TIMEZONE_STR,
    OrderIntent,
)
from natras.config.settings import LiveTradingProhibitedError, Settings


class EntryCutoffViolationError(Exception):
    """Raised when an entry order is submitted at or after the 15:00 IST cutoff."""
    pass


class SafetyGuard:
    """Core safety enforcement guard for trading operations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone_name if settings else MARKET_TIMEZONE_STR)
        self.cutoff_time = time(ENTRY_CUTOFF_HOUR, ENTRY_CUTOFF_MINUTE)

    def verify_paper_only_mode(self) -> None:
        """Verify that live orders are strictly disabled. Fail-closed if not."""
        if self.settings and self.settings.live_orders_enabled:
            raise LiveTradingProhibitedError(
                "FAIL-CLOSED: Live orders are prohibited. Platform is paper-only."
            )

    def to_ist(self, dt: datetime | None = None) -> datetime:
        """Convert a datetime to Asia/Kolkata timezone."""
        if dt is None:
            return datetime.now(self.tz)
        if dt.tzinfo is None:
            # Assume UTC or local naive - localize or replace
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def is_entry_allowed(self, dt: datetime | None = None) -> Tuple[bool, str]:
        """Check whether opening a new position is permitted based on 15:00 IST cutoff."""
        ist_time = self.to_ist(dt)
        current_time = ist_time.time()

        if current_time >= self.cutoff_time:
            return (
                False,
                f"Entry prohibited: Current time {ist_time.strftime('%H:%M:%S %Z')} "
                f"is at or after {self.cutoff_time.strftime('%H:%M')} IST cutoff.",
            )

        return (True, f"Entry permitted at {ist_time.strftime('%H:%M:%S %Z')}.")

    def validate_order_execution(
        self,
        order_intent: OrderIntent | str,
        dt: datetime | None = None,
    ) -> None:
        """Validate an order against paper-only mode and 15:00 IST entry cutoff.

        Raises:
            LiveTradingProhibitedError: If live trading is enabled.
            EntryCutoffViolationError: If an entry order is attempted at or after 15:00 IST.
        """
        # 1. Paper-only check (Fail-closed)
        self.verify_paper_only_mode()

        intent_val = order_intent.value if isinstance(order_intent, OrderIntent) else str(order_intent).upper()

        # 2. Entry cutoff check: Only applies to new position entries
        if intent_val == OrderIntent.ENTRY.value:
            allowed, reason = self.is_entry_allowed(dt)
            if not allowed:
                raise EntryCutoffViolationError(reason)

        # 3. Exits and position management are allowed after 15:00 IST
