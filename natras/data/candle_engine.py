"""Completed 5-minute candle engine with strict validation against data fabrication."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from natras.config.constants import (
    CANDLE_TIMEFRAME_MINUTES,
    MARKET_TIMEZONE_STR,
)
from natras.db.models import CandleRecord


class InvalidCandleError(ValueError):
    """Raised when candle data fails structural or mathematical validity checks."""
    pass


class DiscontinuousDataError(ValueError):
    """Raised when candles violate monotonic order or have unhandled discrepancies."""
    pass


@dataclass(frozen=True)
class Candle:
    """Represents a validated completed 5-minute candle."""
    symbol: str
    timeframe: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr14: Optional[float] = None
    atr: Optional[float] = None

    def to_record(self) -> CandleRecord:
        """Convert to database record format."""
        return CandleRecord(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=self.timestamp.isoformat(),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


def validate_candle(
    symbol: str,
    timestamp: datetime,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    timeframe_minutes: int = CANDLE_TIMEFRAME_MINUTES,
    atr14: Optional[float] = None,
    atr: Optional[float] = None,
) -> Candle:
    """Strictly validate candle data. Never fabricate or invent missing values."""
    if not symbol or not isinstance(symbol, str):
        raise InvalidCandleError("Candle symbol must be a non-empty string.")

    # Price positivity
    for name, price in [("open", open_price), ("high", high_price), ("low", low_price), ("close", close_price)]:
        if price is None or price <= 0:
            raise InvalidCandleError(f"Candle {name} price ({price}) must be strictly positive.")

    # Mathematical integrity of OHLC
    if high_price < low_price:
        raise InvalidCandleError(f"High ({high_price}) cannot be less than low ({low_price}).")
    if high_price < open_price:
        raise InvalidCandleError(f"High ({high_price}) cannot be less than open ({open_price}).")
    if high_price < close_price:
        raise InvalidCandleError(f"High ({high_price}) cannot be less than close ({close_price}).")
    if low_price > open_price:
        raise InvalidCandleError(f"Low ({low_price}) cannot be greater than open ({open_price}).")
    if low_price > close_price:
        raise InvalidCandleError(f"Low ({low_price}) cannot be greater than close ({close_price}).")

    # Volume positivity
    if volume is None or volume < 0:
        raise InvalidCandleError(f"Volume ({volume}) cannot be negative.")

    # Timeframe validation
    if timeframe_minutes != CANDLE_TIMEFRAME_MINUTES:
        raise InvalidCandleError(
            f"Unsupported candle timeframe: {timeframe_minutes}. "
            f"Platform is locked to {CANDLE_TIMEFRAME_MINUTES}-minute completed candles."
        )

    # Timeframe boundary validation (must align with 5-minute boundary)
    if timestamp.tzinfo is None:
        tz = ZoneInfo(MARKET_TIMEZONE_STR)
        timestamp = timestamp.replace(tzinfo=tz)

    if timestamp.minute % CANDLE_TIMEFRAME_MINUTES != 0 or timestamp.second != 0:
        raise InvalidCandleError(
            f"Candle timestamp {timestamp.isoformat()} does not align to completed "
            f"{CANDLE_TIMEFRAME_MINUTES}-minute boundary (minute must be multiple of {CANDLE_TIMEFRAME_MINUTES})."
        )

    return Candle(
        symbol=symbol,
        timeframe=timeframe_minutes,
        timestamp=timestamp,
        open=float(open_price),
        high=float(high_price),
        low=float(low_price),
        close=float(close_price),
        volume=float(volume),
        atr14=float(atr14) if atr14 is not None else None,
        atr=float(atr) if atr is not None else None,
    )


class CandleEngine:
    """Manages ingestion, sequence verification, and history of completed 5-minute candles."""

    def __init__(self, symbol: str, timeframe: int = CANDLE_TIMEFRAME_MINUTES) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self._candles: List[Candle] = []

    @property
    def candles(self) -> List[Candle]:
        """Return a copy of the validated candle series."""
        return list(self._candles)

    @property
    def latest_candle(self) -> Optional[Candle]:
        """Return the most recent completed candle."""
        return self._candles[-1] if self._candles else None

    def add_candle(
        self,
        timestamp: datetime,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        volume: float,
    ) -> Candle:
        """Validate and append a new completed candle."""
        candle = validate_candle(
            symbol=self.symbol,
            timestamp=timestamp,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            timeframe_minutes=self.timeframe,
        )

        if self._candles:
            last = self._candles[-1]
            if candle.timestamp <= last.timestamp:
                raise DiscontinuousDataError(
                    f"Timestamp violation: new candle {candle.timestamp.isoformat()} "
                    f"is not strictly greater than previous candle {last.timestamp.isoformat()}."
                )

        self._candles.append(candle)
        return candle
