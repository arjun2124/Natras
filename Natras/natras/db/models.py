"""Database models and data transfer objects for NAT RAS."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from natras.config.constants import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
)


@dataclass
class OrderRecord:
    """Represents a trading order in the system."""
    client_order_id: str
    strategy_id: str
    symbol: str
    side: OrderSide | str
    order_type: OrderType | str
    intent: OrderIntent | str
    quantity: int
    price: Optional[float] = None
    status: OrderStatus | str = OrderStatus.PENDING
    fill_price: Optional[float] = None
    created_at: Optional[str] = None
    executed_at: Optional[str] = None
    reason: str = ""
    id: Optional[int] = None


@dataclass
class PositionRecord:
    """Represents an active or closed trading position."""
    position_id: str
    strategy_id: str
    symbol: str
    side: OrderSide | str
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: PositionStatus | str = PositionStatus.OPEN
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    realized_pnl: float = 0.0
    id: Optional[int] = None


@dataclass
class TradeJournalRecord:
    """Journal audit log for trade lifecycle events."""
    timestamp: str
    event_type: str
    strategy_id: str
    symbol: str
    payload_json: str
    message: str
    id: Optional[int] = None


@dataclass
class AuditLogRecord:
    """System-level safety and compliance audit log."""
    timestamp: str
    category: str
    level: str
    message: str
    details_json: str = "{}"
    id: Optional[int] = None


@dataclass
class CandleRecord:
    """Completed historical or streaming candle."""
    symbol: str
    timeframe: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
