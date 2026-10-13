from datetime import datetime

import pytest

from natras.data.session import IST
from natras.data.tick_aggregator import InvalidTickError, Tick, TickAggregator

SYM = 'NSE:NIFTY50-INDEX'


def tick(h, m, s, price, vol=0.0, day=14):
    return Tick(SYM, datetime(2026, 10, day, h, m, s, tzinfo=IST), price, vol)


def at(h, m, day=14):
    return datetime(2026, 10, day, h, m, tzinfo=IST)


def test_candle_emitted_only_when_next_bucket_opens():
    agg = TickAggregator(SYM)
    assert agg.add_tick(tick(9, 15, 1, 100, 10)) is None
    assert agg.add_tick(tick(9, 16, 0, 105, 5)) is None
    assert agg.add_tick(tick(9, 17, 0, 99, 1)) is None
    assert agg.add_tick(tick(9, 19, 59, 102, 4)) is None
    candle = agg.add_tick(tick(9, 20, 0, 103))
    assert candle is not None
    assert (candle.open, candle.high, candle.low, candle.close, candle.volume) == (100, 105, 99, 102, 20)
    assert candle.timestamp == at(9, 15)


def test_gap_recorded_not_filled():
    agg = TickAggregator(SYM)
    agg.add_tick(tick(9, 15, 1, 100))
    candle = agg.add_tick(tick(9, 25, 0, 101))
    assert candle is not None and candle.timestamp == at(9, 15)
    assert agg.gaps == [(at(9, 20), at(9, 25))]


def test_close_if_complete_by_clock():
    agg = TickAggregator(SYM)
    agg.add_tick(tick(9, 15, 1, 100))
    assert agg.close_if_complete(datetime(2026, 10, 14, 9, 19, 59, tzinfo=IST)) is None
    candle = agg.close_if_complete(datetime(2026, 10, 14, 9, 20, 0, tzinfo=IST))
    assert candle is not None and candle.close == 100


def test_late_tick_dropped():
    agg = TickAggregator(SYM)
    agg.add_tick(tick(9, 15, 1, 100))
    agg.add_tick(tick(9, 20, 0, 101))
    assert agg.add_tick(tick(9, 17, 0, 100)) is None
    assert agg.late_ticks_dropped == 1


def test_off_session_ticks_dropped():
    agg = TickAggregator(SYM)
    assert agg.add_tick(tick(9, 10, 0, 100)) is None
    assert agg.add_tick(tick(15, 30, 0, 100)) is None
    assert agg.off_session_ticks_dropped == 2


def test_no_gap_across_days():
    agg = TickAggregator(SYM)
    agg.add_tick(tick(15, 25, 0, 100, day=14))
    candle = agg.add_tick(tick(9, 15, 0, 101, day=15))
    assert candle is not None and candle.timestamp == at(15, 25)
    assert agg.gaps == []


@pytest.mark.parametrize('bad', [
    Tick('OTHER', datetime(2026, 10, 14, 9, 15, tzinfo=IST), 100.0),
    Tick(SYM, datetime(2026, 10, 14, 9, 15), 100.0),
    Tick(SYM, datetime(2026, 10, 14, 9, 15, tzinfo=IST), 0.0),
    Tick(SYM, datetime(2026, 10, 14, 9, 15, tzinfo=IST), float('nan')),
    Tick(SYM, datetime(2026, 10, 14, 9, 15, tzinfo=IST), 100.0, -1.0),
])
def test_invalid_ticks_rejected(bad):
    with pytest.raises(InvalidTickError):
        TickAggregator(SYM).add_tick(bad)
