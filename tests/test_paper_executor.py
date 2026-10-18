"""Unit tests for paper execution engine, safety enforcement, and position management."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from natras.config.constants import (
    MARKET_TIMEZONE_STR,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
)
from natras.config.settings import LiveTradingProhibitedError, Settings
from natras.execution.paper_executor import PaperExecutor

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_paper_executor_successful_entry(paper_executor: PaperExecutor):
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    res = paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=50,
        price=24500.0,
        current_dt=dt_morning,
        stop_loss=24450.0,
        take_profit=24600.0,
    )
    assert res.success is True
    assert res.status == OrderStatus.FILLED
    assert res.fill_price == 24500.0
    assert res.position_id is not None

    # Check database persistence
    open_positions = paper_executor.db.get_open_positions()
    assert len(open_positions) == 1
    pos = open_positions[0]
    assert pos.symbol == "NIFTY"
    assert pos.entry_price == 24500.0
    assert pos.stop_loss == 24450.0
    assert pos.take_profit == 24600.0


def test_paper_executor_blocks_entry_at_1500_ist(paper_executor: PaperExecutor):
    dt_cutoff = datetime(2026, 10, 14, 15, 0, 0, tzinfo=TZ)
    res = paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=50,
        price=24500.0,
        current_dt=dt_cutoff,
    )
    assert res.success is False
    assert res.status == OrderStatus.REJECTED
    assert "Safety Violation" in res.message
    assert "15:00 IST cutoff" in res.message

    # Ensure no position opened
    assert len(paper_executor.db.get_open_positions()) == 0


def test_paper_executor_allows_position_exit_after_1500_ist(paper_executor: PaperExecutor):
    # 1. Open in morning
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    entry_res = paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=50,
        price=24500.0,
        current_dt=dt_morning,
    )
    assert entry_res.success is True
    pos_id = entry_res.position_id

    # 2. Close position at 15:15 IST (allowed)
    dt_after = datetime(2026, 10, 14, 15, 15, 0, tzinfo=TZ)
    exit_res = paper_executor.close_position(
        position_id=pos_id,
        exit_price=24550.0,
        current_dt=dt_after,
        reason="EOD_MANAGEMENT",
    )
    assert exit_res.success is True
    assert exit_res.status == OrderStatus.FILLED

    # Check realized PnL: (24550 - 24500) * 50 = +2500
    pos = paper_executor.db.get_position(pos_id)
    assert pos.status == PositionStatus.CLOSED.value
    assert pos.realized_pnl == 2500.0
    assert paper_executor.daily_realized_pnl == 2500.0


def test_paper_executor_short_position_pnl(paper_executor: PaperExecutor):
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    entry_res = paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=50,
        price=24500.0,
        current_dt=dt_morning,
    )
    pos_id = entry_res.position_id

    # Close with profit: entry 24500, exit 24400 -> profit = (24500 - 24400) * 50 = 5000
    exit_res = paper_executor.close_position(
        position_id=pos_id,
        exit_price=24400.0,
        current_dt=dt_morning,
        reason="TARGET_MET",
    )
    assert exit_res.success is True
    pos = paper_executor.db.get_position(pos_id)
    assert pos.realized_pnl == 5000.0


def test_paper_executor_stop_loss_triggered_on_candle(paper_executor: PaperExecutor):
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    entry_res = paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=20,
        price=24500.0,
        stop_loss=24400.0,
        take_profit=24700.0,
        current_dt=dt_morning,
    )
    pos_id = entry_res.position_id

    # Candle dips to 24390 (below SL 24400)
    dt_candle = datetime(2026, 10, 14, 10, 5, 0, tzinfo=TZ)
    results = paper_executor.update_positions_on_candle(
        symbol="NIFTY",
        high=24510.0,
        low=24390.0,
        close=24410.0,
        current_dt=dt_candle,
    )
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].position_id == pos_id

    pos = paper_executor.db.get_position(pos_id)
    assert pos.status == PositionStatus.CLOSED.value
    # PnL: (24400 - 24500) * 20 = -2000
    assert pos.realized_pnl == -2000.0


def test_paper_executor_journal_persisted(paper_executor: PaperExecutor):
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    paper_executor.submit_order(
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=10,
        price=24500.0,
        current_dt=dt_morning,
    )
    entries = paper_executor.db.get_journal_entries()
    assert len(entries) >= 2  # POSITION_OPENED, ORDER_FILLED
    events = [e.event_type for e in entries]
    assert "POSITION_OPENED" in events
    assert "ORDER_FILLED" in events


def test_paper_executor_realistic_slippage_and_brokerage(db, safety_guard, risk_governor, settings):
    executor = PaperExecutor.with_realistic_assumptions(
        db=db,
        safety_guard=safety_guard,
        risk_governor=risk_governor,
        settings=settings,
    )
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)

    # 1. Entry Buy: quoted at 100.0, fills at 100.50
    entry_res = executor.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=65,
        price=100.0,
        current_dt=dt_morning,
    )
    assert entry_res.success is True
    assert entry_res.fill_price == 100.50
    pos_id = entry_res.position_id

    # 2. Exit Sell: quoted at 110.0, fills at 109.50
    dt_exit = datetime(2026, 10, 14, 10, 25, 0, tzinfo=TZ)
    exit_res = executor.close_position(
        position_id=pos_id,
        exit_price=110.0,
        current_dt=dt_exit,
        reason="TRAILING_STOP",
    )
    assert exit_res.success is True
    assert exit_res.fill_price == 109.50

    # Gross PnL = (109.50 - 100.50) * 65 = 585.0
    # Statutory Option Charges:
    #   Brokerage: Rs 40.0
    #   STT (0.1% sell-turnover 7117.50): Rs 7.12
    #   NSE Txn (0.03503% turnover 13650.0): Rs 4.78
    #   Stamp Duty (0.003% buy-turnover 6532.50): Rs 0.20
    #   SEBI + IPFT (0.0001% + 0.0005%): Rs 0.08
    #   GST (18% on Brok 40 + Txn 4.78 + IPFT 0.07): Rs 8.07
    # Total Official Charges = 40.0 + 7.12 + 4.78 + 0.20 + 0.08 + 8.07 = 60.25
    # True Net PnL = 585.0 - 60.25 = 524.75
    pos = executor.db.get_position(pos_id)
    assert pos.realized_pnl == 524.75
    assert executor.daily_realized_pnl == 524.75


def test_paper_executor_rejects_duplicate_order_id(paper_executor: PaperExecutor):
    """Verify duplicate client_order_id submission is rejected immediately."""
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    cid = "custom_ord_123"

    res1 = paper_executor.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=65,
        price=100.0,
        current_dt=dt_morning,
        client_order_id=cid,
    )
    assert res1.success is True

    # Resubmission with same client_order_id must be rejected
    res2 = paper_executor.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=65,
        price=100.0,
        current_dt=dt_morning,
        client_order_id=cid,
    )
    assert res2.success is False
    assert res2.status.value == "REJECTED"
    assert "Duplicate client_order_id" in res2.message


def test_paper_executor_rejects_unmatched_exit(paper_executor: PaperExecutor):
    """Verify EXIT intent with no matching open position is rejected without phantom fill."""
    dt_morning = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)

    res = paper_executor.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350PE",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        intent=OrderIntent.EXIT,
        quantity=65,
        price=50.0,
        current_dt=dt_morning,
    )
    assert res.success is False
    assert res.status.value == "REJECTED"
    assert "No open position found" in res.message


def test_paper_executor_daily_counters_survive_restart(db, safety_guard, risk_governor, settings):
    """Verify daily_realized_pnl and daily_trades_count survive process restart."""
    exec1 = PaperExecutor.with_realistic_assumptions(db, safety_guard, risk_governor, settings)
    dt_now = datetime.now(TZ).replace(hour=10, minute=0, second=0)

    # Execute trade and close it
    entry = exec1.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=65,
        price=100.0,
        current_dt=dt_now,
    )
    assert entry.success is True
    exit_res = exec1.close_position(
        position_id=entry.position_id,
        exit_price=110.0,
        current_dt=dt_now,
        reason="TARGET_REACHED",
    )
    assert exit_res.success is True
    assert exec1.daily_realized_pnl == 524.75
    assert exec1.daily_trades_count == 2  # 1 entry + 1 exit

    # Create new instance simulating process restart
    exec2 = PaperExecutor.with_realistic_assumptions(db, safety_guard, risk_governor, settings)
    assert exec2.daily_realized_pnl == 524.75
    assert exec2.daily_trades_count == 2


def test_paper_executor_compute_option_charges_breakdown(db, safety_guard, risk_governor, settings):
    """Test detailed breakdown of all Indian statutory option taxes and broker charges."""
    from natras.execution.paper_executor import compute_option_charges

    charges = compute_option_charges(
        entry_price=100.50,
        exit_price=109.50,
        quantity=65,
        brokerage_per_order=20.0,
        is_buy_entry=True,
        include_statutory_taxes=True,
    )
    assert charges.brokerage == 40.0
    assert charges.stt == 7.12
    assert charges.txn_fee == 4.78
    assert charges.stamp_duty == 0.20
    assert charges.sebi_ipft == 0.08
    assert charges.gst == 8.07
    assert charges.total_charges == 60.25


def test_paper_executor_toggle_statutory_taxes_off(db, safety_guard, risk_governor, settings):
    """Verify that statutory taxes can be optionally toggled off for pure brokerage simulations."""
    executor = PaperExecutor.with_realistic_assumptions(
        db=db,
        safety_guard=safety_guard,
        risk_governor=risk_governor,
        settings=settings,
        include_statutory_taxes=False,
    )
    dt_now = datetime.now(TZ).replace(hour=10, minute=0, second=0)

    entry = executor.submit_order(
        strategy_id="v2.2.0-RESEARCH-MOMENTUM-WICK",
        symbol="NSE:NIFTY26O1317350CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        intent=OrderIntent.ENTRY,
        quantity=65,
        price=100.0,
        current_dt=dt_now,
    )
    exit_res = executor.close_position(
        position_id=entry.position_id,
        exit_price=110.0,
        current_dt=dt_now,
        reason="TARGET_REACHED",
    )
    # Without statutory taxes: Gross 585.0 - Brokerage 40.0 = 545.0
    pos = executor.db.get_position(entry.position_id)
    assert pos.realized_pnl == 545.0
    assert executor.daily_realized_pnl == 545.0



