"""Paper trading execution engine with fail-closed safety and persistent state."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from natras.config.constants import (
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
)
from natras.config.settings import Settings
from natras.db.database import DatabaseManager
from natras.db.models import (
    AuditLogRecord,
    OrderRecord,
    PositionRecord,
    TradeJournalRecord,
)
from natras.risk.governor import RiskGovernor
from natras.safety.guards import SafetyGuard


@dataclass
class ExecutionResult:
    """Result of an order execution attempt."""
    success: bool
    order_id: str
    status: OrderStatus
    message: str
    fill_price: Optional[float] = None
    position_id: Optional[str] = None


class PaperExecutor:
    """Manages paper order execution, simulated fills, and position lifecycle.

    Guarantees:
    - Never communicates with live broker endpoints.
    - Aborts immediately if LIVE_ORDERS_ENABLED is True.
    - Blocks new position entries at or after 15:00 IST.
    - Allows exit and risk management orders at any market time.
    - All actions are committed to SQLite journal and order tables.
    """

    def __init__(
        self,
        db: DatabaseManager,
        safety_guard: SafetyGuard,
        risk_governor: RiskGovernor,
        settings: Settings,
        entry_slippage: float = 0.0,
        exit_slippage: float = 0.0,
        brokerage_per_order: float = 0.0,
        risk_profile_id: str = "v2.2.0-research-momentum-wick-default",
    ) -> None:
        self.db = db
        self.safety_guard = safety_guard
        self.risk_governor = risk_governor
        self.settings = settings
        self.entry_slippage = entry_slippage
        self.exit_slippage = exit_slippage
        self.brokerage_per_order = brokerage_per_order
        self.risk_profile_id = risk_profile_id
        self.daily_realized_pnl = 0.0
        self.daily_trades_count = 0

    @classmethod
    def with_realistic_assumptions(
        cls,
        db: DatabaseManager,
        safety_guard: SafetyGuard,
        risk_governor: RiskGovernor,
        settings: Settings,
    ) -> "PaperExecutor":
        """Factory initializing PaperExecutor with realistic slippage and brokerage."""
        return cls(
            db=db,
            safety_guard=safety_guard,
            risk_governor=risk_governor,
            settings=settings,
            entry_slippage=0.50,
            exit_slippage=0.50,
            brokerage_per_order=20.0,
        )

    def submit_order(
        self,
        strategy_id: str,
        symbol: str,
        side: OrderSide | str,
        order_type: OrderType | str,
        intent: OrderIntent | str,
        quantity: int,
        price: float,
        current_dt: Optional[datetime] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Submit a paper order with safety, risk, and entry cutoff validation."""
        side_val = OrderSide(side).value if isinstance(side, str) else side.value
        type_val = OrderType(order_type).value if isinstance(order_type, str) else order_type.value
        intent_val = OrderIntent(intent).value if isinstance(intent, str) else intent.value
        cid = client_order_id or f"paper_{uuid.uuid4().hex[:12]}"
        now_ist = self.safety_guard.to_ist(current_dt)
        now_iso = now_ist.isoformat()

        order = OrderRecord(
            client_order_id=cid,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side_val,
            order_type=type_val,
            intent=intent_val,
            quantity=quantity,
            price=price,
            status=OrderStatus.PENDING.value,
            created_at=now_iso,
        )

        # 1. Safety Guard Verification (Paper-only fail-closed + 15:00 IST entry cutoff)
        try:
            self.safety_guard.validate_order_execution(order_intent=intent_val, dt=now_ist)
        except Exception as e:
            order.status = OrderStatus.REJECTED.value
            order.reason = f"Safety Violation: {str(e)}"
            self.db.save_order(order)
            self._journal_event(
                event_type="ORDER_REJECTED",
                strategy_id=strategy_id,
                symbol=symbol,
                payload={"client_order_id": cid, "reason": order.reason},
                message=f"Order {cid} rejected by SafetyGuard: {e}",
            )
            return ExecutionResult(
                success=False,
                order_id=cid,
                status=OrderStatus.REJECTED,
                message=order.reason,
            )

        # 2. Risk Governor Verification
        open_positions = self.db.get_open_positions(strategy_id=strategy_id)
        passed, risk_msg = self.risk_governor.evaluate_order(
            order=order,
            open_positions=open_positions,
            daily_realized_pnl=self.daily_realized_pnl,
            daily_trades_count=self.daily_trades_count,
        )
        if not passed:
            order.status = OrderStatus.REJECTED.value
            order.reason = f"Risk Violation: {risk_msg}"
            self.db.save_order(order)
            self._journal_event(
                event_type="ORDER_REJECTED",
                strategy_id=strategy_id,
                symbol=symbol,
                payload={"client_order_id": cid, "reason": order.reason},
                message=f"Order {cid} rejected by RiskGovernor: {risk_msg}",
            )
            return ExecutionResult(
                success=False,
                order_id=cid,
                status=OrderStatus.REJECTED,
                message=order.reason,
            )

        # 3. Paper Order Fill Simulation (Executed at provided real market price + slippage)
        if side_val == OrderSide.BUY.value:
            slip = self.entry_slippage if intent_val == OrderIntent.ENTRY.value else self.exit_slippage
            fill_price = round(price + slip, 2)
        else:
            slip = self.entry_slippage if intent_val == OrderIntent.ENTRY.value else self.exit_slippage
            fill_price = round(price - slip, 2)

        order.status = OrderStatus.FILLED.value
        order.fill_price = fill_price
        order.executed_at = now_iso
        self.db.save_order(order)
        self.daily_trades_count += 1

        position_id: Optional[str] = None

        if intent_val == OrderIntent.ENTRY.value:
            # Create new open position
            position_id = f"pos_{uuid.uuid4().hex[:12]}"
            pos = PositionRecord(
                position_id=position_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=side_val,
                quantity=quantity,
                entry_price=fill_price,
                current_price=fill_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=PositionStatus.OPEN.value,
                opened_at=now_iso,
            )
            self.db.save_position(pos)
            self._journal_event(
                event_type="POSITION_OPENED",
                strategy_id=strategy_id,
                symbol=symbol,
                payload={
                    "position_id": position_id,
                    "side": side_val,
                    "qty": quantity,
                    "price": fill_price,
                    "quoted_price": price,
                    "slippage": slip,
                    "risk_profile_id": self.risk_profile_id,
                },
                message=f"Opened position {position_id} for {symbol} at {fill_price} (quoted {price}, slippage {slip})",
            )
        else:
            # EXIT intent: matched to existing open position for this symbol
            matching_positions = [p for p in open_positions if p.symbol == symbol]
            if matching_positions:
                target_pos = matching_positions[0]
                position_id = target_pos.position_id
                self._close_position_record(
                    pos=target_pos,
                    exit_price=fill_price,
                    closed_at=now_iso,
                    reason=f"Order {cid} exit",
                )

        self._journal_event(
            event_type="ORDER_FILLED",
            strategy_id=strategy_id,
            symbol=symbol,
            payload={
                "client_order_id": cid,
                "fill_price": fill_price,
                "position_id": position_id,
                "slippage": slip,
            },
            message=f"Paper order {cid} filled at {fill_price} (quoted {price}, slippage {slip})",
        )

        return ExecutionResult(
            success=True,
            order_id=cid,
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            position_id=position_id,
            message="Paper order filled successfully.",
        )

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        current_dt: Optional[datetime] = None,
        reason: str = "MANUAL_CLOSE",
    ) -> ExecutionResult:
        """Explicitly close an existing position (allowed after 15:00 IST)."""
        # Paper check
        self.safety_guard.verify_paper_only_mode()

        pos = self.db.get_position(position_id)
        if not pos or str(pos.status) != PositionStatus.OPEN.value:
            return ExecutionResult(
                success=False,
                order_id="",
                status=OrderStatus.REJECTED,
                message=f"Position {position_id} not found or already closed.",
            )

        now_ist = self.safety_guard.to_ist(current_dt)
        exit_side = OrderSide.SELL.value if pos.side == OrderSide.BUY.value else OrderSide.BUY.value
        if exit_side == OrderSide.SELL.value:
            actual_fill_price = round(exit_price - self.exit_slippage, 2)
        else:
            actual_fill_price = round(exit_price + self.exit_slippage, 2)

        # Submit exit order
        cid = f"exit_{uuid.uuid4().hex[:12]}"
        order = OrderRecord(
            client_order_id=cid,
            strategy_id=pos.strategy_id,
            symbol=pos.symbol,
            side=exit_side,
            order_type=OrderType.MARKET.value,
            intent=OrderIntent.EXIT.value,
            quantity=pos.quantity,
            price=exit_price,
            fill_price=actual_fill_price,
            status=OrderStatus.FILLED.value,
            created_at=now_ist.isoformat(),
            executed_at=now_ist.isoformat(),
            reason=reason,
        )
        self.db.save_order(order)
        self.daily_trades_count += 1

        self._close_position_record(
            pos=pos,
            exit_price=actual_fill_price,
            closed_at=now_ist.isoformat(),
            reason=reason,
        )

        return ExecutionResult(
            success=True,
            order_id=cid,
            status=OrderStatus.FILLED,
            fill_price=actual_fill_price,
            position_id=position_id,
            message=f"Position {position_id} closed at {actual_fill_price} ({reason}).",
        )

    def update_positions_on_candle(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        current_dt: Optional[datetime] = None,
    ) -> List[ExecutionResult]:
        """Update open positions on completed candle; check stop loss and take profit."""
        # Live orders check
        self.safety_guard.verify_paper_only_mode()

        results: List[ExecutionResult] = []
        open_positions = self.db.get_open_positions()

        for pos in open_positions:
            if pos.symbol != symbol:
                continue

            # Update current price
            pos.current_price = close
            self.db.save_position(pos)

            # Check stop loss / take profit triggers
            if str(pos.side) == OrderSide.BUY.value:
                # Long position: SL triggers if low <= stop_loss
                if pos.stop_loss is not None and low <= pos.stop_loss:
                    res = self.close_position(
                        position_id=pos.position_id,
                        exit_price=pos.stop_loss,
                        current_dt=current_dt,
                        reason="STOP_LOSS_TRIGGERED",
                    )
                    results.append(res)
                # TP triggers if high >= take_profit
                elif pos.take_profit is not None and high >= pos.take_profit:
                    res = self.close_position(
                        position_id=pos.position_id,
                        exit_price=pos.take_profit,
                        current_dt=current_dt,
                        reason="TAKE_PROFIT_TRIGGERED",
                    )
                    results.append(res)
            elif str(pos.side) == OrderSide.SELL.value:
                # Short position: SL triggers if high >= stop_loss
                if pos.stop_loss is not None and high >= pos.stop_loss:
                    res = self.close_position(
                        position_id=pos.position_id,
                        exit_price=pos.stop_loss,
                        current_dt=current_dt,
                        reason="STOP_LOSS_TRIGGERED",
                    )
                    results.append(res)
                # TP triggers if low <= take_profit
                elif pos.take_profit is not None and low <= pos.take_profit:
                    res = self.close_position(
                        position_id=pos.position_id,
                        exit_price=pos.take_profit,
                        current_dt=current_dt,
                        reason="TAKE_PROFIT_TRIGGERED",
                    )
                    results.append(res)

        return results

    def _close_position_record(
        self,
        pos: PositionRecord,
        exit_price: float,
        closed_at: str,
        reason: str,
    ) -> None:
        """Calculate realized PnL and persist closed position."""
        if str(pos.side) == OrderSide.BUY.value:
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        total_brokerage = round(self.brokerage_per_order * 2.0, 2)  # ₹20 entry + ₹20 exit
        net_pnl = round(gross_pnl - total_brokerage, 2)

        pos.current_price = exit_price
        pos.closed_at = closed_at
        pos.status = PositionStatus.CLOSED.value
        pos.realized_pnl = net_pnl
        self.db.save_position(pos)

        self.daily_realized_pnl += pos.realized_pnl

        self._journal_event(
            event_type="POSITION_CLOSED",
            strategy_id=pos.strategy_id,
            symbol=pos.symbol,
            payload={
                "position_id": pos.position_id,
                "exit_price": exit_price,
                "gross_pnl": round(gross_pnl, 2),
                "brokerage": total_brokerage,
                "realized_pnl": pos.realized_pnl,
                "risk_profile_id": self.risk_profile_id,
                "reason": reason,
            },
            message=(
                f"Closed position {pos.position_id} with net PnL {pos.realized_pnl:.2f} "
                f"(Gross: {gross_pnl:.2f}, Brokerage: {total_brokerage:.2f}) [{reason}]"
            ),
        )

    def _journal_event(
        self,
        event_type: str,
        strategy_id: str,
        symbol: str,
        payload: dict,
        message: str,
    ) -> None:
        """Write structured audit journal entry."""
        entry = TradeJournalRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            strategy_id=strategy_id,
            symbol=symbol,
            payload_json=json.dumps(payload),
            message=message,
        )
        self.db.log_journal(entry)
