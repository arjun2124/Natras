"""Unit tests for Research Backtester."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.candle_engine import Candle, validate_candle
from natras.research.backtester import Backtester

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_backtester_replay_run(tmp_path):
    report_dir = tmp_path / "runtime_data" / "reports" / "research"

    # Build 10 candles: 9 normal, 1 strong bullish momentum candle at 10:00
    base_time = datetime(2026, 10, 14, 9, 20, 0, tzinfo=TZ)
    candles = []
    for i in range(15):
        t = base_time + timedelta(minutes=5 * i)
        if i == 8:  # 10:00 IST
            c = validate_candle("NSE:NIFTY50-INDEX", t, 24500.0, 24535.0, 24495.0, 24530.0, 100000.0, atr14=15.0)
        elif i > 8:
            # Continuing up
            c = validate_candle("NSE:NIFTY50-INDEX", t, 24530.0, 24560.0, 24525.0, 24555.0, 80000.0, atr14=15.0)
        else:
            c = validate_candle("NSE:NIFTY50-INDEX", t, 24490.0, 24505.0, 24485.0, 24500.0, 20000.0, atr14=15.0)
        candles.append(c)

    # Force square off at 15:00
    eod_candle = validate_candle("NSE:NIFTY50-INDEX", datetime(2026, 10, 14, 15, 0, 0, tzinfo=TZ), 24555.0, 24560.0, 24550.0, 24555.0, 10000.0, atr14=15.0)
    candles.append(eod_candle)

    backtester = Backtester()
    res = backtester.run(candles_list=candles)

    assert res.total_candles == len(candles)
    assert res.total_trades >= 1
    assert res.trades[0].direction == "CE"
    assert res.trades[0].entry_fill == 100.50  # 100.0 + entry slippage 0.50
    assert res.trades[0].brokerage == 40.0
