"""Unit tests for risk governor limits and circuit breakers."""

import pytest

from natras.config.constants import OrderIntent, OrderSide, OrderType
from natras.db.models import OrderRecord, PositionRecord
from natras.risk.governor import RiskGovernor, RiskLimits, RiskViolationError


def _make_order(intent: OrderIntent = OrderIntent.ENTRY, quantity: int = 50) -> OrderRecord:
    return OrderRecord(
        client_order_id="test_ord_1",
        strategy_id="v2.2.0-baseline",
        symbol="NIFTY",
        side=OrderSide.BUY.value,
        order_type=OrderType.MARKET.value,
        intent=intent.value,
        quantity=quantity,
        price=100.0,
    )


def test_risk_allows_normal_entry():
    governor = RiskGovernor(RiskLimits(max_daily_loss=5000.0, max_position_size=100, max_open_positions=2))
    order = _make_order(quantity=50)
    passed, msg = governor.evaluate_order(order, open_positions=[], daily_realized_pnl=0.0, daily_trades_count=0)
    assert passed is True
    assert "passed" in msg


def test_risk_blocks_oversized_position():
    governor = RiskGovernor(RiskLimits(max_position_size=100))
    order = _make_order(quantity=150)
    passed, msg = governor.evaluate_order(order, open_positions=[], daily_realized_pnl=0.0, daily_trades_count=0)
    assert passed is False
    assert "exceeds maximum allowed position size" in msg

    with pytest.raises(RiskViolationError):
        governor.validate_or_raise(order, open_positions=[], daily_realized_pnl=0.0, daily_trades_count=0)


def test_risk_blocks_on_daily_loss_circuit_breaker():
    governor = RiskGovernor(RiskLimits(max_daily_loss=5000.0))
    order = _make_order(quantity=20)
    # Loss of 5500 breaches 5000 max loss
    passed, msg = governor.evaluate_order(order, open_positions=[], daily_realized_pnl=-5500.0, daily_trades_count=2)
    assert passed is False
    assert "Circuit breaker active" in msg


def test_risk_blocks_when_max_open_positions_reached():
    governor = RiskGovernor(RiskLimits(max_open_positions=2))
    order = _make_order(quantity=20)
    existing_positions = [
        PositionRecord(position_id="p1", strategy_id="s1", symbol="NIFTY", side="BUY", quantity=25, entry_price=100, current_price=100),
        PositionRecord(position_id="p2", strategy_id="s1", symbol="BANKNIFTY", side="BUY", quantity=15, entry_price=200, current_price=200),
    ]
    passed, msg = governor.evaluate_order(order, open_positions=existing_positions, daily_realized_pnl=0.0, daily_trades_count=2)
    assert passed is False
    assert "Max open positions reached" in msg


def test_risk_blocks_when_max_daily_trades_reached():
    governor = RiskGovernor(RiskLimits(max_daily_trades=5))
    order = _make_order(quantity=20)
    passed, msg = governor.evaluate_order(order, open_positions=[], daily_realized_pnl=0.0, daily_trades_count=5)
    assert passed is False
    assert "Daily trade limit reached" in msg


def test_risk_always_permits_exit_order():
    governor = RiskGovernor(RiskLimits(max_daily_loss=5000.0, max_open_positions=1))
    exit_order = _make_order(intent=OrderIntent.EXIT, quantity=1000)  # Even oversized exit
    existing_positions = [
        PositionRecord(position_id="p1", strategy_id="s1", symbol="NIFTY", side="BUY", quantity=1000, entry_price=100, current_price=90),
    ]
    # Even if daily loss is breached, closing positions to reduce risk is permitted
    passed, msg = governor.evaluate_order(exit_order, open_positions=existing_positions, daily_realized_pnl=-6000.0, daily_trades_count=10)
    assert passed is True
    assert "Exit order approved" in msg
