"""Unit tests for SQLite database manager and models."""

from natras.config.constants import OrderIntent, OrderSide, OrderStatus, OrderType, PositionStatus
from natras.db.database import DatabaseManager
from natras.db.models import (
    AuditLogRecord,
    CandleRecord,
    OrderRecord,
    PositionRecord,
    TradeJournalRecord,
)


def test_db_initialization():
    db = DatabaseManager(":memory:")
    with db.get_connection() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "orders" in table_names
        assert "positions" in table_names
        assert "trade_journal" in table_names
        assert "audit_log" in table_names
        assert "candles" in table_names


def test_db_order_crud(db: DatabaseManager):
    order = OrderRecord(
        client_order_id="ord_100",
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY.value,
        order_type=OrderType.MARKET.value,
        intent=OrderIntent.ENTRY.value,
        quantity=50,
        price=24500.0,
        status=OrderStatus.PENDING.value,
    )
    db.save_order(order)

    fetched = db.get_order("ord_100")
    assert fetched is not None
    assert fetched.symbol == "NIFTY"
    assert fetched.quantity == 50
    assert fetched.status == OrderStatus.PENDING.value

    # Update order
    order.status = OrderStatus.FILLED.value
    order.fill_price = 24505.0
    db.save_order(order)

    updated = db.get_order("ord_100")
    assert updated.status == OrderStatus.FILLED.value
    assert updated.fill_price == 24505.0


def test_db_position_crud(db: DatabaseManager):
    pos = PositionRecord(
        position_id="pos_200",
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY.value,
        quantity=50,
        entry_price=24500.0,
        current_price=24500.0,
        stop_loss=24400.0,
        take_profit=24700.0,
        status=PositionStatus.OPEN.value,
        opened_at="2026-10-14T09:30:00+05:30",
    )
    db.save_position(pos)

    fetched = db.get_position("pos_200")
    assert fetched is not None
    assert fetched.symbol == "NIFTY"
    assert fetched.status == PositionStatus.OPEN.value

    open_positions = db.get_open_positions()
    assert len(open_positions) == 1

    # Close position
    pos.status = PositionStatus.CLOSED.value
    pos.current_price = 24600.0
    pos.realized_pnl = 5000.0
    db.save_position(pos)

    assert len(db.get_open_positions()) == 0
    closed_pos = db.get_position("pos_200")
    assert closed_pos.status == PositionStatus.CLOSED.value
    assert closed_pos.realized_pnl == 5000.0


def test_db_journal_and_audit(db: DatabaseManager):
    j_entry = TradeJournalRecord(
        timestamp="2026-10-14T09:30:00+05:30",
        event_type="TEST_EVENT",
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        payload_json='{"test": true}',
        message="Test journal message",
    )
    j_id = db.log_journal(j_entry)
    assert j_id > 0

    entries = db.get_journal_entries()
    assert len(entries) == 1
    assert entries[0].event_type == "TEST_EVENT"

    a_entry = AuditLogRecord(
        timestamp="2026-10-14T09:30:00+05:30",
        category="SAFETY",
        level="INFO",
        message="Paper mode verified",
    )
    a_id = db.log_audit(a_entry)
    assert a_id > 0


def test_db_candle_storage(db: DatabaseManager):
    c = CandleRecord(
        symbol="NIFTY",
        timeframe=5,
        timestamp="2026-10-14T09:20:00+05:30",
        open=24500.0,
        high=24520.0,
        low=24490.0,
        close=24510.0,
        volume=1000.0,
    )
    db.save_candle(c)

    retrieved = db.get_recent_candles("NIFTY", timeframe=5, limit=10)
    assert len(retrieved) == 1
    assert retrieved[0].open == 24500.0
    assert retrieved[0].close == 24510.0
