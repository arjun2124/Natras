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
        "NSE:NIFTY26O1524500CE": {
            "bid": 99.5,
            "ask": 100.0,
            "ltp": 100.0,
            "volume": 5000,
        }
    }
    quote_fetcher = lambda syms: mock_quotes

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
    mock_quotes = {
        "NSE:NIFTY26O1524500CE": {
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
        quote_fetcher=lambda syms: mock_quotes,
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

    # 1. Price climbs to +4.5 PP -> locks in stop at +1.0 PP
    res1 = runtime.update_positions_on_option_price(
        symbol="NSE:NIFTY26O1524500CE",
        current_price=entry_fill + 4.5,
        current_dt=datetime(2026, 10, 14, 10, 10, 0, tzinfo=TZ),
    )
    assert len(res1) == 0  # not stopped out
    assert tracker.trailing_stop.current_stop_pp == 1.0

    # 2. Price climbs to +9.0 PP -> trails stop at peak - 3.0 = +6.0 PP
    res2 = runtime.update_positions_on_option_price(
        symbol="NSE:NIFTY26O1524500CE",
        current_price=entry_fill + 9.0,
        current_dt=datetime(2026, 10, 14, 10, 15, 0, tzinfo=TZ),
    )
    assert len(res2) == 0
    assert tracker.trailing_stop.current_stop_pp == 6.0

    # 3. Price drops to breach stop (current_price = entry_fill + 5.5, stop level is +6.0)
    res3 = runtime.update_positions_on_option_price(
        symbol="NSE:NIFTY26O1524500CE",
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
