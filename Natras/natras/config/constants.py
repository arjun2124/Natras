"""System-wide constants and enumerations for NAT RAS."""

from enum import Enum

# Core Strategy Family Lock
STRATEGY_FAMILY = "v2.2.0-RESEARCH-MOMENTUM-WICK"

# Time & Market Constraints
MARKET_TIMEZONE_STR = "Asia/Kolkata"
ENTRY_CUTOFF_HOUR = 15
ENTRY_CUTOFF_MINUTE = 0
CANDLE_TIMEFRAME_MINUTES = 5

# Market Trading Hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderIntent(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class StrategyStatus(str, Enum):
    AWAITING_SOURCE = "AWAITING_SOURCE"
    ACTIVE_PAPER = "ACTIVE_PAPER"
    SHADOW = "SHADOW"
    RETIRED = "RETIRED"


class ModuleStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    PARTIAL = "PARTIAL"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
