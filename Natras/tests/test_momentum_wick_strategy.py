"""Deterministic unit tests for v2.2.0-RESEARCH-MOMENTUM-WICK strategy."""

from datetime import datetime, time
from zoneinfo import ZoneInfo
import pytest

from natras.config.constants import MARKET_TIMEZONE_STR, OrderIntent, OrderSide
from natras.data.candle_engine import Candle, validate_candle
from natras.strategies.momentum_wick import (
    MomentumWickStrategy,
    SteppedTrailingStop,
)

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def _make_candle(
    hour: int = 10,
    minute: int = 0,
    open_p: float = 24500.0,
    high_p: float = 24530.0,
    low_p: float = 24490.0,
    close_p: float = 24525.0,
    timeframe: int = 5,
    atr14: float | None = None,
    atr: float | None = None,
) -> Candle:
    dt = datetime(2026, 10, 14, hour, minute, 0, tzinfo=TZ)
    return Candle(
        symbol="NIFTY",
        timeframe=timeframe,
        timestamp=dt,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=10000.0,
        atr14=atr14,
        atr=atr,
    )


# 1. Timeframe Rejection
def test_timeframe_rejection():
    strat = MomentumWickStrategy()
    # 3-minute candle should be rejected
    c_3m = _make_candle(hour=10, minute=0, timeframe=3)
    signals = strat.on_candle(c_3m)
    assert len(signals) == 0
    assert "Timeframe mismatch" in strat.last_rejection_reason


# 2. Trading Hours Rejection (09:20 to 14:45 IST)
def test_trading_hours_rejection_before_0920():
    strat = MomentumWickStrategy()
    # 09:15 candle -> rejected (before 09:20)
    c_early = _make_candle(hour=9, minute=15)
    signals = strat.on_candle(c_early)
    assert len(signals) == 0
    assert "before entry start" in strat.last_rejection_reason


def test_trading_hours_acceptance_at_0920_and_1445():
    strat = MomentumWickStrategy()
    # 09:20 candle with valid range and body -> accepted
    c_0920 = _make_candle(hour=9, minute=20, open_p=24500, high_p=24530, low_p=24490, close_p=24525, atr14=20.0)
    signals_0920 = strat.on_candle(c_0920)
    assert len(signals_0920) == 1

    # 14:45 candle with valid range and body -> accepted
    c_1445 = _make_candle(hour=14, minute=45, open_p=24500, high_p=24530, low_p=24490, close_p=24525, atr14=20.0)
    signals_1445 = strat.on_candle(c_1445)
    assert len(signals_1445) == 1


def test_trading_hours_rejection_after_1445():
    strat = MomentumWickStrategy()
    # 14:50 candle -> rejected (after 14:45)
    c_late = _make_candle(hour=14, minute=50)
    signals = strat.on_candle(c_late)
    assert len(signals) == 0
    assert "after entry cutoff" in strat.last_rejection_reason


# 3. ATR Threshold Pass/Fail
def test_atr_threshold_fail():
    strat = MomentumWickStrategy(min_range_atr_ratio=1.0)
    # Range is 10 (24510 - 24500), but ATR is 20.0. Range < 1.0 * ATR
    c = _make_candle(hour=10, minute=0, open_p=24502, high_p=24510, low_p=24500, close_p=24509, atr14=20.0)
    signals = strat.on_candle(c)
    assert len(signals) == 0
    assert "ATR range threshold failed" in strat.last_rejection_reason


def test_atr_threshold_pass():
    strat = MomentumWickStrategy(min_range_atr_ratio=1.0)
    # Range is 25 (24525 - 24500) >= 20.0 ATR. Range >= 1.0 * ATR
    c = _make_candle(hour=10, minute=0, open_p=24505, high_p=24525, low_p=24500, close_p=24520, atr14=20.0)
    signals = strat.on_candle(c)
    assert len(signals) == 1


# 4. Body-to-Range Ratio Pass/Fail
def test_body_ratio_threshold_fail():
    strat = MomentumWickStrategy(min_body_ratio=0.45)
    # Range is 40 (24540 - 24500). Body is 10 (24520 - 24510). Ratio is 10/40 = 0.25 < 0.45
    c = _make_candle(hour=10, minute=0, open_p=24510, high_p=24540, low_p=24500, close_p=24520, atr14=15.0)
    signals = strat.on_candle(c)
    assert len(signals) == 0
    assert "Body-to-range ratio failed" in strat.last_rejection_reason


def test_body_ratio_threshold_pass():
    strat = MomentumWickStrategy(min_body_ratio=0.45)
    # Range is 40. Body is 20 (24530 - 24510). Ratio is 20/40 = 0.50 >= 0.45
    c = _make_candle(hour=10, minute=0, open_p=24510, high_p=24540, low_p=24500, close_p=24530, atr14=15.0)
    signals = strat.on_candle(c)
    assert len(signals) == 1


# 5. Bullish Candle => CE Signal
def test_bullish_candle_generates_ce_signal():
    strat = MomentumWickStrategy()
    # close (24530) > open (24505) -> Bullish
    c = _make_candle(hour=10, minute=0, open_p=24505, high_p=24535, low_p=24500, close_p=24530, atr14=15.0)
    signals = strat.on_candle(c)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.metadata["instrument_type"] == "CE"
    assert sig.metadata["technical_sl"] == c.low  # candle.low for CE
    assert "Bullish" in sig.reason


# 6. Bearish Candle => PE Signal
def test_bearish_candle_generates_pe_signal():
    strat = MomentumWickStrategy()
    # close (24505) < open (24530) -> Bearish
    c = _make_candle(hour=10, minute=0, open_p=24530, high_p=24535, low_p=24500, close_p=24505, atr14=15.0)
    signals = strat.on_candle(c)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.metadata["instrument_type"] == "PE"
    assert sig.metadata["technical_sl"] == c.high  # candle.high for PE
    assert "Bearish" in sig.reason


# 7. Doji Rejection
def test_doji_candle_rejection():
    strat = MomentumWickStrategy()
    # close == open (24515 == 24515) -> Doji
    c = _make_candle(hour=10, minute=0, open_p=24515, high_p=24535, low_p=24500, close_p=24515, atr14=15.0)
    signals = strat.on_candle(c)
    assert len(signals) == 0
    assert "Doji candle" in strat.last_rejection_reason


# 8. Baseline vs High-Conviction Classification
def test_variant_classification():
    strat = MomentumWickStrategy()
    # Case A: Range 40, Body 20 -> Ratio 0.50 (>= 0.45, < 0.65) => BASELINE
    c_baseline = _make_candle(hour=10, minute=0, open_p=24510, high_p=24540, low_p=24500, close_p=24530, atr14=15.0)
    sig_baseline = strat.on_candle(c_baseline)[0]
    assert sig_baseline.metadata["variant"] == "BASELINE"

    # Case B: Range 40, Body 30 -> Ratio 0.75 (>= 0.65) => HIGH_CONVICTION
    c_hc = _make_candle(hour=10, minute=5, open_p=24505, high_p=24540, low_p=24500, close_p=24535, atr14=15.0)
    sig_hc = strat.on_candle(c_hc)[0]
    assert sig_hc.metadata["variant"] == "HIGH_CONVICTION"


# 9. ATR Precedence Hierarchy (All 5 Tiers)
def test_atr_precedence_tier_1_candle_atr14():
    strat = MomentumWickStrategy()
    c = _make_candle(atr14=25.0, atr=18.0)
    context = {"market_metrics": {"atr14": 12.0}}
    atr, source = strat.resolve_atr(c, context)
    assert atr == 25.0
    assert source == "candle.atr14"


def test_atr_precedence_tier_2_candle_atr():
    strat = MomentumWickStrategy()
    c = _make_candle(atr14=None, atr=18.0)
    context = {"market_metrics": {"atr14": 12.0}}
    atr, source = strat.resolve_atr(c, context)
    assert atr == 18.0
    assert source == "candle.atr"


def test_atr_precedence_tier_3_context_market_metrics():
    strat = MomentumWickStrategy()
    c = _make_candle(atr14=None, atr=None)
    context = {"market_metrics": {"atr14": 12.0}}
    atr, source = strat.resolve_atr(c, context)
    assert atr == 12.0
    assert source == "context.market_metrics['atr14']"


def test_atr_precedence_tier_4_rolling_tr_approximation():
    strat = MomentumWickStrategy(atr_period=14)
    # Feed 14 candles with Range 20 each into history
    for i in range(14):
        c = _make_candle(hour=10, minute=i * 5 % 60, open_p=100, high_p=120, low_p=100, close_p=110)
        strat.history.append(c)

    c_new = _make_candle(atr14=None, atr=None)
    atr, source = strat.resolve_atr(c_new, context=None)
    assert atr == 20.0
    assert source == "rolling_tr_approximation"


def test_atr_precedence_tier_5_fallback_default():
    strat = MomentumWickStrategy()
    strat.history.clear()
    c = _make_candle(atr14=None, atr=None)
    atr, source = strat.resolve_atr(c, context=None)
    assert atr == 15.0
    assert source == "fallback_default_15.0"


# 10. Technical Stop Loss for CE and PE
def test_technical_sl_spot_levels():
    strat = MomentumWickStrategy()
    c_ce = _make_candle(open_p=24500, high_p=24530, low_p=24490, close_p=24525, atr14=15.0)
    sig_ce = strat.on_candle(c_ce)[0]
    assert sig_ce.metadata["technical_sl"] == 24490.0  # candle low

    c_pe = _make_candle(open_p=24525, high_p=24530, low_p=24490, close_p=24500, atr14=15.0)
    sig_pe = strat.on_candle(c_pe)[0]
    assert sig_pe.metadata["technical_sl"] == 24530.0  # candle high


# 11. on_tick Produces No Entry
def test_on_tick_produces_no_entry():
    strat = MomentumWickStrategy()
    signals = strat.on_tick({"symbol": "NIFTY", "ltp": 24520.0})
    assert signals == []


# 12. No Fixed Target
def test_no_fixed_target():
    strat = MomentumWickStrategy()
    c = _make_candle(open_p=24500, high_p=24530, low_p=24490, close_p=24525, atr14=15.0)
    sig = strat.on_candle(c)[0]
    assert sig.target_price is None
    assert sig.take_profit is None
    assert sig.metadata["fixed_target_pp"] is None


# 13, 14, 15, 16. Stepped Trailing Stop Invariants
def test_stepped_trailing_stop_lifecycle():
    ts = SteppedTrailingStop(
        initial_stop_pp=15.0,
        lock_in_trigger_pp=4.0,
        lock_in_level_pp=1.0,
        trail_trigger_pp=8.0,
        trail_gap_pp=3.0,
    )
    # Initial state: -15.0 PP
    assert ts.current_stop_pp == -15.0

    # Slight favorable movement: +2.0 PP -> stop remains -15.0 PP
    ts.update(2.0)
    assert ts.current_stop_pp == -15.0
    assert not ts.is_stopped_out(-5.0)

    # Trigger Tier 1: +4.0 PP -> Stop locks to +1.0 PP
    stop = ts.update(4.0)
    assert stop == 1.0
    assert ts.current_stop_pp == 1.0

    # Intermediate favorable: +7.0 PP -> Stop remains +1.0 PP
    ts.update(7.0)
    assert ts.current_stop_pp == 1.0

    # Trigger Tier 2: +8.0 PP -> Stop trails at 8.0 - 3.0 = +5.0 PP
    stop = ts.update(8.0)
    assert stop == 5.0
    assert ts.current_stop_pp == 5.0

    # Further run-up: +11.0 PP -> Stop trails at 11.0 - 3.0 = +8.0 PP
    stop = ts.update(11.0)
    assert stop == 8.0
    assert ts.current_stop_pp == 8.0

    # Pullback to +6.0 PP -> Trailing stop NEVER loosens
    stop_after_pullback = ts.update(6.0)
    assert stop_after_pullback == 8.0
    assert ts.current_stop_pp == 8.0

    # Check stopped out: current PnL of +7.5 PP <= +8.0 PP stop level
    assert ts.is_stopped_out(7.5) is True
    assert ts.is_stopped_out(8.5) is False


# 17. Forced EOD Square-Off Time Check
def test_forced_eod_square_off_time_check():
    strat = MomentumWickStrategy(eod_square_off_time=time(15, 0))

    dt_before = datetime(2026, 10, 14, 14, 55, 0, tzinfo=TZ)
    assert strat.is_eod_square_off_time(dt_before) is False

    dt_at_cutoff = datetime(2026, 10, 14, 15, 0, 0, tzinfo=TZ)
    assert strat.is_eod_square_off_time(dt_at_cutoff) is True

    dt_after_cutoff = datetime(2026, 10, 14, 15, 15, 0, tzinfo=TZ)
    assert strat.is_eod_square_off_time(dt_after_cutoff) is True
