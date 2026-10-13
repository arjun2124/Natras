"""Unit tests for PaperRuntime end-to-end execution, option selection,
trailing stop management, and Telegram alerts.
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from natras.config.constants import MARKET_TIMEZONE_STR, OrderIntent, OrderSide, OrderType
from natras.data.candle_engine import validate_candle
from natras.execution.paper_runtime import PaperRuntime
from natras.options.strike_selector import OptionChain
from natras.options.symbol_master import OptionContract

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


@pytest.fixture
def sample_option_chain():
    """Option chain with nearest expiry strikes around 24500."""
    expiry = date(2026, 10, 15)
    contracts = [
        OptionContract(
            fytoken="101",
            description="NIFTY 24450 CE",
            symbol="NSE:NIFTY26O1524450CE",
            expiry_epoch=1792047600,
            expiry_date=expiry,
            strike=24450.0,
            option_type="CE",
            lot_size=65,
            tick_size=0.05,
        ),
        OptionContract(
            fytoken="102",
            description="NIFTY 24500 CE",
            symbol="NSE:NIFTY26O1524500CE",
            expiry_epoch=1792047600,
            expiry_date=expiry,
            strike=24500.0,
            option_type="CE",
            lot_size=65,
            tick_size=0.05,
        ),
        OptionContract(
            fytoken="103",
            description="NIFTY 24550 CE",
            symbol="NSE:NIFTY26O1524550CE",
            expiry_epoch=1792047600,
            expiry_date=expiry,
            strike=24550.0,
            option_type="CE",
            lot_size=65,
            tick_size=0.05,
        ),
        OptionContract(
            fytoken="201",
            description="NIFTY 24500 PE",
            symbol="NSE:NIFTY26O1524500PE",
            expiry_epoch=1792047600,
            expiry_date=expiry,
            strike=24500.0,
            option_type="PE",
            lot_size=65,
            tick_size=0.05,
        ),
    ]
    return OptionChain(contracts)


def test_paper_runtime_entry_lifecycle(db, settings, sample_option_chain):
    mock_telegram = MagicMock()
    mock_quotes = {
        "bid": 99.5,
        "ask": 100.0,
        "ltp": 100.0,
        "volume": 5000,
    }
    quote_fetcher = lambda syms: {s: dict(mock_quotes) for s in syms}

    runtime = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
        quote_fetcher=quote_fetcher,
    )

    # 1. Bullish candle at 10:00 IST (valid window)
    dt_candle = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    candle = validate_candle(
        "NSE:NIFTY50-INDEX",
        dt_candle,
        24480.0,
        24510.0,
        24475.0,
        24505.0,
        50000.0,
        atr14=15.0,
    )

    results = runtime.on_spot_candle(candle)
    assert len(results) == 1
    res = results[0]
    assert res.success is True
    # Fill price includes entry slippage (+0.50) -> 100.0 + 0.50 = 100.50
    assert res.fill_price == 100.50
    assert res.position_id in runtime.active_trackers

    # Telegram notification sent
    assert mock_telegram.send_message.called
    assert "PAPER ENTRY EXECUTED" in mock_telegram.send_message.call_args_list[0][0][0]


def test_paper_runtime_trailing_stop_lifecycle(db, settings, sample_option_chain):
    mock_telegram = MagicMock()
    mock_quote = {
        "bid": 99.5,
        "ask": 100.0,
        "ltp": 100.0,
        "volume": 5000,
    }
    runtime = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
        quote_fetcher=lambda syms: {s: dict(mock_quote) for s in syms},
    )

    # Open entry
    dt_candle = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    candle = validate_candle(
        "NSE:NIFTY50-INDEX",
        dt_candle,
        24480.0,
        24510.0,
        24475.0,
        24505.0,
        50000.0,
        atr14=15.0,
    )
    runtime.on_spot_candle(candle)
    pos_id = list(runtime.active_trackers.keys())[0]
    tracker = runtime.active_trackers[pos_id]
    entry_fill = tracker.entry_price  # 100.50
    contract_sym = tracker.symbol

    # 1. Price climbs to +4.5 PP -> locks in stop at +1.0 PP
    res1 = runtime.update_positions_on_option_price(
        symbol=contract_sym,
        current_price=entry_fill + 4.5,
        current_dt=datetime(2026, 10, 14, 10, 10, 0, tzinfo=TZ),
    )
    assert len(res1) == 0  # not stopped out
    assert tracker.trailing_stop.current_stop_pp == 1.0

    # 2. Price climbs to +9.0 PP -> trails stop at peak - 3.0 = +6.0 PP
    res2 = runtime.update_positions_on_option_price(
        symbol=contract_sym,
        current_price=entry_fill + 9.0,
        current_dt=datetime(2026, 10, 14, 10, 15, 0, tzinfo=TZ),
    )
    assert len(res2) == 0
    assert tracker.trailing_stop.current_stop_pp == 6.0

    # 3. Price drops to breach stop (current_price = entry_fill + 5.5, stop level is +6.0)
    res3 = runtime.update_positions_on_option_price(
        symbol=contract_sym,
        current_price=entry_fill + 5.5,
        current_dt=datetime(2026, 10, 14, 10, 20, 0, tzinfo=TZ),
    )
    assert len(res3) == 1
    assert res3[0].success is True
    assert pos_id not in runtime.active_trackers

    # Check closed position in DB
    pos = db.get_position(pos_id)
    assert pos.status == "CLOSED"
    assert pos.realized_pnl > 0.0  # Profitable trade net of brokerage & slippage


def test_paper_runtime_blocks_entry_at_1500_ist(db, settings, sample_option_chain):
    mock_telegram = MagicMock()
    runtime = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
    )

    dt_1500 = datetime(2026, 10, 14, 15, 0, 0, tzinfo=TZ)
    candle = validate_candle(
        "NSE:NIFTY50-INDEX",
        dt_1500,
        24480.0,
        24510.0,
        24475.0,
        24505.0,
        50000.0,
        atr14=15.0,
    )

    results = runtime.on_spot_candle(candle, current_dt=dt_1500)
    assert len(results) == 0
    assert len(runtime.active_trackers) == 0


def test_paper_runtime_poll_and_update_open_positions(db, settings, sample_option_chain):
    """Verify production polling of live option quotes operates the stepped trailing stop."""
    mock_telegram = MagicMock()
    live_quotes = {
        "NSE:NIFTY26O1524550CE": {
            "bid": 99.5,
            "ask": 100.0,
            "ltp": 100.0,
            "volume": 5000,
        }
    }
    runtime = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
        quote_fetcher=lambda syms: live_quotes,
    )

    # 1. Open entry
    dt_candle = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    candle = validate_candle(
        "NSE:NIFTY50-INDEX",
        dt_candle,
        24480.0,
        24510.0,
        24475.0,
        24505.0,
        50000.0,
        atr14=15.0,
    )
    res_entry = runtime.on_spot_candle(candle)
    assert len(res_entry) == 1
    pos_id = list(runtime.active_trackers.keys())[0]
    contract_sym = runtime.active_trackers[pos_id].symbol

    # 2. Simulate quote climbing to +9.0 PP favorable
    live_quotes[contract_sym] = {
        "bid": 109.0,
        "ask": 109.5,
        "ltp": 109.5,
        "volume": 6000,
    }
    dt_climb = datetime(2026, 10, 14, 10, 15, 0, tzinfo=TZ)
    res_poll = runtime.poll_and_update_open_positions(current_dt=dt_climb)
    assert len(res_poll) == 0  # not stopped out
    tracker = runtime.active_trackers[pos_id]
    # Stop should have ratcheted up
    assert tracker.trailing_stop.current_stop_pp > 0.0

    # 3. Simulate sudden drop below ratcheted stop
    live_quotes[contract_sym] = {
        "bid": 90.0,
        "ask": 90.5,
        "ltp": 90.0,
        "volume": 8000,
    }
    dt_drop = datetime(2026, 10, 14, 10, 20, 0, tzinfo=TZ)
    res_stopout = runtime.poll_and_update_open_positions(current_dt=dt_drop)
    assert len(res_stopout) == 1
    assert res_stopout[0].success is True
    assert pos_id not in runtime.active_trackers


def test_paper_runtime_restart_recovery(db, settings, sample_option_chain):
    """Verify open positions in database are recovered into active_trackers on restart."""
    mock_telegram = MagicMock()
    runtime1 = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
        quote_fetcher=lambda syms: {s: {"bid": 99.5, "ask": 100.0, "ltp": 100.0, "volume": 5000} for s in syms},
    )

    dt_candle = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    candle = validate_candle(
        "NSE:NIFTY50-INDEX",
        dt_candle,
        24480.0,
        24510.0,
        24475.0,
        24505.0,
        50000.0,
        atr14=15.0,
    )
    runtime1.on_spot_candle(candle)
    pos_id = list(runtime1.active_trackers.keys())[0]

    # Simulate restart by creating a new PaperRuntime instance pointing to same db
    runtime2 = PaperRuntime(
        db=db,
        settings=settings,
        option_chain=sample_option_chain,
        telegram_bot=mock_telegram,
        quote_fetcher=lambda syms: {s: {"bid": 99.5, "ask": 100.0, "ltp": 100.0, "volume": 5000} for s in syms},
    )

    # Position state must survive restart
    assert pos_id in runtime2.active_trackers
    recovered = runtime2.active_trackers[pos_id]
    assert recovered.symbol == runtime1.active_trackers[pos_id].symbol
    assert recovered.entry_price == runtime1.active_trackers[pos_id].entry_price


def test_quote_validation_finiteness_and_staleness(settings):
    """Verify validate_option_quote rejects NaN, Inf, non-positive, and stale quotes."""
    from natras.risk.strategy_profiles import get_default_risk_profile
    profile = get_default_risk_profile()
    now_ref = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)

    # 1. NaN quote
    nan_quote = {"bid": float("nan"), "ask": 100.0, "ltp": 100.0, "volume": 5000}
    valid, reason = profile.validate_option_quote(nan_quote, current_dt=now_ref)
    assert valid is False
    assert "Non-finite" in reason

    # 2. Infinity quote
    inf_quote = {"bid": 99.0, "ask": float("inf"), "ltp": 100.0, "volume": 5000}
    valid, reason = profile.validate_option_quote(inf_quote, current_dt=now_ref)
    assert valid is False
    assert "Non-finite" in reason

    # 3. Non-positive LTP
    zero_quote = {"bid": 99.0, "ask": 100.0, "ltp": 0.0, "volume": 5000}
    valid, reason = profile.validate_option_quote(zero_quote, current_dt=now_ref)
    assert valid is False
    assert "strictly positive" in reason

    # 4. Stale timestamp (from 2020)
    stale_quote = {
        "bid": 99.5,
        "ask": 100.0,
        "ltp": 100.0,
        "volume": 5000,
        "ts": "2020-01-01T10:00:00+05:30",
    }
    valid, reason = profile.validate_option_quote(stale_quote, current_dt=now_ref)
    assert valid is False
    assert "Stale" in reason

    # 5. Fresh valid quote
    fresh_quote = {
        "bid": 99.5,
        "ask": 100.0,
        "ltp": 100.0,
        "volume": 5000,
        "ts": "2026-10-14T10:00:00+05:30",
    }
    valid, reason = profile.validate_option_quote(fresh_quote, current_dt=now_ref)
    assert valid is True
