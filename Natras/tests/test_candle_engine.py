"""Unit tests for completed 5-minute candle engine and anti-fabrication rules."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.candle_engine import (
    CandleEngine,
    DiscontinuousDataError,
    InvalidCandleError,
    validate_candle,
)

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_valid_candle_creation():
    dt = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    candle = validate_candle(
        symbol="NIFTY",
        timestamp=dt,
        open_price=24500.0,
        high_price=24525.0,
        low_price=24490.0,
        close_price=24510.0,
        volume=125000.0,
    )
    assert candle.symbol == "NIFTY"
    assert candle.timeframe == 5
    assert candle.open == 24500.0
    assert candle.high == 24525.0
    assert candle.low == 24490.0
    assert candle.close == 24510.0
    assert candle.volume == 125000.0


def test_candle_rejects_non_positive_price():
    dt = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    with pytest.raises(InvalidCandleError, match="must be strictly positive"):
        validate_candle("NIFTY", dt, 0.0, 24525.0, 24490.0, 24510.0, 1000.0)

    with pytest.raises(InvalidCandleError, match="must be strictly positive"):
        validate_candle("NIFTY", dt, 24500.0, -10.0, 24490.0, 24510.0, 1000.0)


def test_candle_rejects_corrupted_high_low():
    dt = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    # High < Low
    with pytest.raises(InvalidCandleError, match="High .* cannot be less than low"):
        validate_candle("NIFTY", dt, 24500.0, 24450.0, 24490.0, 24500.0, 1000.0)

    # High < Open
    with pytest.raises(InvalidCandleError, match="High .* cannot be less than open"):
        validate_candle("NIFTY", dt, 24550.0, 24500.0, 24400.0, 24450.0, 1000.0)

    # Low > Close
    with pytest.raises(InvalidCandleError, match="Low .* cannot be greater than close"):
        validate_candle("NIFTY", dt, 24500.0, 24550.0, 24480.0, 24450.0, 1000.0)


def test_candle_rejects_negative_volume():
    dt = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    with pytest.raises(InvalidCandleError, match="Volume .* cannot be negative"):
        validate_candle("NIFTY", dt, 24500.0, 24520.0, 24490.0, 24510.0, -50.0)


def test_candle_rejects_unaligned_time():
    # 09:23 is not aligned to a 5-minute boundary
    dt_unaligned = datetime(2026, 10, 14, 9, 23, 0, tzinfo=TZ)
    with pytest.raises(InvalidCandleError, match="does not align to completed 5-minute boundary"):
        validate_candle("NIFTY", dt_unaligned, 24500.0, 24520.0, 24490.0, 24510.0, 1000.0)

    # 09:20:15 with seconds
    dt_seconds = datetime(2026, 10, 14, 9, 20, 15, tzinfo=TZ)
    with pytest.raises(InvalidCandleError, match="does not align to completed 5-minute boundary"):
        validate_candle("NIFTY", dt_seconds, 24500.0, 24520.0, 24490.0, 24510.0, 1000.0)


def test_candle_engine_monotonicity():
    engine = CandleEngine(symbol="NIFTY")

    dt1 = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    dt2 = datetime(2026, 10, 14, 9, 25, 0, tzinfo=TZ)

    c1 = engine.add_candle(dt1, 100, 105, 95, 102, 500)
    assert engine.latest_candle == c1

    c2 = engine.add_candle(dt2, 102, 108, 101, 107, 600)
    assert engine.latest_candle == c2
    assert len(engine.candles) == 2

    # Reject duplicate timestamp
    with pytest.raises(DiscontinuousDataError, match="Timestamp violation"):
        engine.add_candle(dt2, 107, 110, 106, 109, 400)

    # Reject past timestamp
    with pytest.raises(DiscontinuousDataError, match="Timestamp violation"):
        engine.add_candle(dt1, 100, 105, 95, 102, 500)
