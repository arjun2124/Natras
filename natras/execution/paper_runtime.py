"""Paper trading runtime orchestrating live candles, strategy evaluation,
option selection, quote verification, trailing stop management, and Telegram alerts.

Implements Phase 3 of the NAT RAS Master Project Draft:
- Completed 5-minute candles ingestion
- Baseline v2.2.0-RESEARCH-MOMENTUM-WICK strategy evaluation
- Dynamic nearest-expiry strike selection from official symbol master
- Realistic quote validation (spread, volume)
- Paper execution with slippage (₹0.50) and brokerage (₹20 per order)
- Stepped trailing stop management (-15 PP initial, +4 PP lock, +8 PP trail)
- Non-bypassable 15:00 IST entry cutoff and EOD square-off
- Comprehensive Telegram alerts and SQLite audit trail
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from natras.adapters.telegram_live import TelegramLiveBot
from natras.config.constants import (
    MARKET_TIMEZONE_STR,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionStatus,
)
from natras.config.settings import Settings, load_settings
from natras.data.candle_engine import Candle
from natras.data.session import IST
from natras.db.database import DatabaseManager
from natras.db.models import PositionRecord
from natras.execution.paper_executor import ExecutionResult, PaperExecutor
from natras.options.strike_selector import (
    Moneyness,
    OptionChain,
    StrikeSelectionError,
    select_contract,
    select_contract_by_delta,
)
from natras.options.symbol_master import OptionContract, load_nifty_options
from natras.risk.governor import RiskGovernor
from natras.risk.strategy_profiles import StrategyRiskProfile, get_default_risk_profile
from natras.safety.guards import SafetyGuard
from natras.strategies.base import Signal
from natras.strategies.momentum_wick import MomentumWickStrategy, SteppedTrailingStop

logger = logging.getLogger(__name__)


@dataclass
class ActivePositionTracker:
    """In-memory active position state tracking the dynamic stepped trailing stop."""
    position_id: str
    symbol: str
    option_type: str
    strike: float
    entry_price: float
    quantity: int
    opened_at: datetime
    trailing_stop: SteppedTrailingStop
    last_known_price: float
    highest_favorable_pnl_pp: float = 0.0


class PaperRuntime:
    """End-to-end paper trading orchestrator on live/completed candles."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        settings: Optional[Settings] = None,
        risk_profile: Optional[StrategyRiskProfile] = None,
        option_chain: Optional[OptionChain] = None,
        telegram_bot: Optional[TelegramLiveBot] = None,
        quote_fetcher: Optional[Callable[[List[str]], Dict[str, Any]]] = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.db = db or DatabaseManager(self.settings.database_path)
        self.safety_guard = SafetyGuard(self.settings)
        self.risk_governor = RiskGovernor(settings=self.settings)
        self.risk_profile = risk_profile or get_default_risk_profile()
        self.strategy = MomentumWickStrategy()

        # Paper executor with realistic slippage and brokerage
        self.executor = PaperExecutor(
            db=self.db,
            safety_guard=self.safety_guard,
            risk_governor=self.risk_governor,
            settings=self.settings,
            entry_slippage=self.risk_profile.entry_slippage,
            exit_slippage=self.risk_profile.exit_slippage,
            brokerage_per_order=self.risk_profile.brokerage_per_order,
            risk_profile_id=self.risk_profile.risk_profile_id,
        )

        self.telegram = telegram_bot or TelegramLiveBot(db_manager=self.db)
        self.quote_fetcher = quote_fetcher
        self.tz = ZoneInfo(MARKET_TIMEZONE_STR)

        # In-memory tracking of active option positions & trailing stops
        self.active_trackers: Dict[str, ActivePositionTracker] = {}
        self.chain = option_chain

        # Phase 3 Requirement: Recover active open positions from database on startup
        self.recover_open_positions()

    def recover_open_positions(self) -> int:
        """Recover active open positions from SQLite database across restarts."""
        open_positions = self.db.get_open_positions()
        recovered_count = 0
        for pos in open_positions:
            if pos.position_id in self.active_trackers:
                continue

            trailing = self.strategy.create_trailing_stop()
            # Restore trailing stop state if current price indicates favorable movement
            if pos.current_price is not None and pos.entry_price is not None:
                current_pnl_pp = pos.current_price - pos.entry_price
                if current_pnl_pp > 0:
                    trailing.update(current_pnl_pp)

            opened_dt = (
                datetime.fromisoformat(pos.opened_at)
                if pos.opened_at
                else datetime.now(self.tz)
            )

            tracker = ActivePositionTracker(
                position_id=pos.position_id,
                symbol=pos.symbol,
                option_type="CE" if "CE" in pos.symbol else "PE",
                strike=0.0,
                entry_price=pos.entry_price or 0.0,
                quantity=pos.quantity or 0,
                opened_at=opened_dt,
                trailing_stop=trailing,
                last_known_price=pos.current_price or pos.entry_price or 0.0,
            )
            self.active_trackers[pos.position_id] = tracker
            recovered_count += 1

        if recovered_count > 0:
            logger.info("Successfully recovered %d active position trackers on restart.", recovered_count)
        return recovered_count

    def load_option_chain_from_master(self, master_path: Optional[str] = None) -> None:
        """Load option chain from downloaded FYERS NSE_FO symbol master."""
        try:
            contracts, _ = load_nifty_options(master_path) if master_path else load_nifty_options()
            self.chain = OptionChain(contracts)
            logger.info("Loaded OptionChain with %d NIFTY option contracts.", len(contracts))
        except Exception as exc:
            logger.warning("Could not load symbol master: %s", exc)

    def on_spot_candle(
        self,
        candle: Candle,
        current_dt: Optional[datetime] = None,
    ) -> List[ExecutionResult]:
        """Process completed 5-minute spot candle from data feed.

        1. Evaluates candle against MomentumWickStrategy rules.
        2. If signal generated and 15:00 cutoff not reached:
           - Selects ATM/ITM option contract via OptionChain.
           - Fetches real quote and validates spread/volume.
           - Submits paper entry order with slippage and brokerage.
           - Initializes SteppedTrailingStop tracker.
           - Notifies Telegram.
        3. Updates trailing stops on any existing positions.
        """
        results: List[ExecutionResult] = []
        now = current_dt or candle.timestamp
        now_ist = self.safety_guard.to_ist(now)

        # Verify paper-only fail-closed invariant
        self.safety_guard.verify_paper_only_mode()

        # Check EOD square-off cutoff (15:00 / 15:15 IST)
        if self.strategy.is_eod_square_off_time(now_ist):
            eod_results = self.square_off_all_positions(now_ist, reason="EOD_SQUARE_OFF_1500_IST")
            results.extend(eod_results)
            return results

        # 1. Evaluate candle in strategy
        signals = self.strategy.on_candle(candle)
        if not signals:
            logger.debug("Candle %s produced no signal: %s", candle.timestamp, self.strategy.last_rejection_reason)
            return results

        for signal in signals:
            res = self._handle_signal(signal, candle, now_ist)
            if res:
                results.append(res)

        return results

    def _handle_signal(
        self,
        signal: Signal,
        candle: Candle,
        now_ist: datetime,
    ) -> Optional[ExecutionResult]:
        """Convert strategy signal into validated paper option order."""
        # Check hard 15:00 IST entry cutoff
        allowed, reason = self.safety_guard.is_entry_allowed(now_ist)
        if not allowed:
            logger.warning("Signal rejected by entry cutoff: %s", reason)
            self.telegram.send_message(
                f"⚠️ *SIGNAL REJECTED BY 15:00 IST CUTOFF*\n"
                f"• Signal: {signal.reason}\n"
                f"• Time: {now_ist.strftime('%H:%M:%S')}\n"
                f"• Reason: {reason}"
            )
            return None

        # Check if active positions exist and multiple positions allowed
        open_positions = self.db.get_open_positions(strategy_id=signal.strategy_id)
        if open_positions and not self.strategy.allow_multiple_positions:
            logger.info("Signal skipped: position already open and multiple positions disabled.")
            return None

        # Resolve option contract from OptionChain
        if self.chain is None:
            self.load_option_chain_from_master()
        if self.chain is None:
            logger.error("Cannot execute signal: OptionChain not loaded.")
            return None

        option_type = signal.metadata.get("instrument_type", "CE")
        today = now_ist.date()

        try:
            if getattr(self.risk_profile, "target_delta", None) is not None:
                selection = select_contract_by_delta(
                    chain=self.chain,
                    spot=candle.close,
                    option_type=option_type,
                    target_delta=self.risk_profile.target_delta,
                    today=today,
                )
            else:
                selection = select_contract(
                    chain=self.chain,
                    spot=candle.close,
                    option_type=option_type,
                    moneyness=Moneyness.parse(self.risk_profile.preferred_moneyness),
                    today=today,
                )
        except StrikeSelectionError as exc:
            logger.error("Option strike selection failed: %s", exc)
            self.telegram.send_message(f"❌ *OPTION SELECTION FAILED*\n• Error: {exc}")
            return None

        contract_symbol = selection.contract.symbol
        lot_size = selection.lot_size

        # Fetch and validate live quote for option contract (freshness & finiteness)
        quoted_price = self._get_and_validate_option_quote(contract_symbol, current_dt=now_ist)
        if quoted_price is None:
            logger.warning("Order aborted: Option quote unavailable or failed validation for %s", contract_symbol)
            return None

        # Submit paper order via PaperExecutor
        order_qty = signal.quantity * lot_size
        res = self.executor.submit_order(
            strategy_id=signal.strategy_id,
            symbol=contract_symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            intent=OrderIntent.ENTRY,
            quantity=order_qty,
            price=quoted_price,
            current_dt=now_ist,
            stop_loss=quoted_price - self.risk_profile.initial_stop_pp,
        )

        if res.success and res.position_id:
            # Initialize stepped trailing stop tracker
            trailing = self.strategy.create_trailing_stop()
            tracker = ActivePositionTracker(
                position_id=res.position_id,
                symbol=contract_symbol,
                option_type=option_type,
                strike=selection.contract.strike,
                entry_price=res.fill_price or quoted_price,
                quantity=order_qty,
                opened_at=now_ist,
                trailing_stop=trailing,
                last_known_price=res.fill_price or quoted_price,
            )
            self.active_trackers[res.position_id] = tracker

            # Send Telegram alert
            self.telegram.send_message(
                f"🟢 *PAPER ENTRY EXECUTED*\n"
                f"• Strategy: {signal.strategy_id}\n"
                f"• Contract: `{contract_symbol}`\n"
                f"• Spot Close: ₹{candle.close:.2f} ({signal.metadata.get('variant')})\n"
                f"• Fill Price: ₹{res.fill_price:.2f} (Quoted: ₹{quoted_price:.2f}, Slip: ₹{self.risk_profile.entry_slippage})\n"
                f"• Quantity: {order_qty} ({signal.quantity} lot)\n"
                f"• Initial Stop: ₹{tracker.entry_price - self.risk_profile.initial_stop_pp:.2f} (-{self.risk_profile.initial_stop_pp} PP)"
            )

        return res

    def _get_and_validate_option_quote(
        self,
        symbol: str,
        current_dt: Optional[datetime] = None,
    ) -> Optional[float]:
        """Fetch quote and validate against spread, liquidity, finiteness, and staleness requirements."""
        if self.quote_fetcher is None:
            logger.warning("No quote fetcher configured; cannot obtain real option quote.")
            return None

        try:
            quotes = self.quote_fetcher([symbol])
            q = quotes.get(symbol)
            if not q or not isinstance(q, dict):
                return None

            valid, reason = self.risk_profile.validate_option_quote(q, current_dt=current_dt)
            if not valid:
                logger.warning("Quote rejected by risk profile for %s: %s", symbol, reason)
                self.telegram.send_message(
                    f"⚠️ *OPTION QUOTE REJECTED*\n"
                    f"• Symbol: `{symbol}`\n"
                    f"• Reason: {reason}"
                )
                return None

            # Quoted entry price is ask price for BUY order
            ask = float(q.get("ask") or q.get("a_price") or q.get("ltp") or q.get("close", 0))
            if not math.isfinite(ask) or ask <= 0:
                logger.warning("Invalid non-finite or non-positive ask price for %s: %s", symbol, ask)
                return None
            return ask
        except Exception as exc:
            logger.error("Failed to fetch quote for %s: %s", symbol, exc)
            return None

    def poll_and_update_open_positions(
        self,
        current_dt: Optional[datetime] = None,
    ) -> List[ExecutionResult]:
        """Poll live option quotes for open positions and operate stepped trailing stops.

        Fulfills Step 6 of docs/FINAL_PLAN.md:
        'Monitor genuine option premiums to operate the existing stepped trailing stop.'
        """
        if not self.active_trackers:
            return []

        now_ist = self.safety_guard.to_ist(current_dt)

        # Check EOD square-off cutoff (15:00 / 15:15 IST)
        if self.strategy.is_eod_square_off_time(now_ist):
            return self.square_off_all_positions(now_ist, reason="EOD_SQUARE_OFF_1500_IST")

        if self.quote_fetcher is None:
            return []

        symbols = list(set(t.symbol for t in self.active_trackers.values()))
        results: List[ExecutionResult] = []
        try:
            quotes = self.quote_fetcher(symbols)
            if not quotes or not isinstance(quotes, dict):
                return []

            for sym in symbols:
                q = quotes.get(sym)
                if not q or not isinstance(q, dict):
                    continue

                valid, reason = self.risk_profile.validate_option_quote(q, current_dt=now_ist)
                if not valid:
                    logger.warning("Live quote for %s failed validation: %s", sym, reason)
                    continue

                ltp = float(q.get("ltp") or q.get("close") or q.get("bid", 0.0))
                if not math.isfinite(ltp) or ltp <= 0:
                    continue

                res_list = self.update_positions_on_option_price(sym, ltp, now_ist)
                results.extend(res_list)
        except Exception as exc:
            logger.error("Error polling open option position quotes: %s", exc)

        return results

    def update_positions_on_option_price(
        self,
        symbol: str,
        current_price: float,
        current_dt: Optional[datetime] = None,
    ) -> List[ExecutionResult]:
        """Update active positions on incoming option quote or candle.

        Ratchets the stepped trailing stop upward and checks for stop-out breaches.
        """
        if not math.isfinite(current_price) or current_price <= 0:
            logger.warning("Rejected non-finite/non-positive option price for %s: %s", symbol, current_price)
            return []

        results: List[ExecutionResult] = []
        now_ist = self.safety_guard.to_ist(current_dt)

        # Find trackers for this symbol
        matching = [t for t in self.active_trackers.values() if t.symbol == symbol]
        for tracker in matching:
            tracker.last_known_price = current_price
            pnl_pp = current_price - tracker.entry_price

            # Update SteppedTrailingStop
            old_stop_pp = tracker.trailing_stop.current_stop_pp
            updated_stop_pp = tracker.trailing_stop.update(pnl_pp)

            # Detect stop ratcheting for Telegram alert
            if updated_stop_pp > old_stop_pp:
                current_stop_price = tracker.entry_price + updated_stop_pp
                if updated_stop_pp == self.risk_profile.lock_in_level_pp:
                    event_desc = f"Stop Locked at +{self.risk_profile.lock_in_level_pp} PP"
                else:
                    event_desc = f"Trailing Stop Ratcheted to {updated_stop_pp:+.2f} PP"

                self.telegram.send_message(
                    f"🔒 *{event_desc.upper()}*\n"
                    f"• Symbol: `{symbol}`\n"
                    f"• Current Price: ₹{current_price:.2f} ({pnl_pp:+.2f} PP)\n"
                    f"• New Stop Price: ₹{current_stop_price:.2f}"
                )

            # Check if stopped out
            if tracker.trailing_stop.is_stopped_out(pnl_pp):
                stop_exit_price = tracker.entry_price + tracker.trailing_stop.current_stop_pp
                exit_price = min(current_price, stop_exit_price)
                res = self.executor.close_position(
                    position_id=tracker.position_id,
                    exit_price=exit_price,
                    current_dt=now_ist,
                    reason="STEPPED_TRAILING_STOP_TRIGGERED",
                )
                if res.success:
                    results.append(res)
                    del self.active_trackers[tracker.position_id]

                    pos = self.db.get_position(tracker.position_id)
                    net_pnl = pos.realized_pnl if pos else 0.0

                    self.telegram.send_message(
                        f"🔴 *PAPER POSITION CLOSED (STOP-OUT)*\n"
                        f"• Contract: `{symbol}`\n"
                        f"• Exit Price: ₹{res.fill_price:.2f} (Quoted: ₹{exit_price:.2f})\n"
                        f"• Net Realized PnL: ₹{net_pnl:.2f}\n"
                        f"• Reason: Trailing Stop Breached"
                    )

        return results

    def square_off_all_positions(
        self,
        current_dt: Optional[datetime] = None,
        reason: str = "EOD_SQUARE_OFF",
    ) -> List[ExecutionResult]:
        """Liquidate all open paper positions (e.g. at 15:00 IST EOD cutoff)."""
        results: List[ExecutionResult] = []
        now_ist = self.safety_guard.to_ist(current_dt)

        open_positions = self.db.get_open_positions()
        for pos in open_positions:
            tracker = self.active_trackers.get(pos.position_id)
            candidate = tracker.last_known_price if tracker else pos.current_price
            if candidate is not None and math.isfinite(candidate) and candidate > 0:
                exit_price = candidate
            else:
                exit_price = pos.entry_price or 100.0

            res = self.executor.close_position(
                position_id=pos.position_id,
                exit_price=exit_price,
                current_dt=now_ist,
                reason=reason,
            )
            if res.success:
                results.append(res)
                if pos.position_id in self.active_trackers:
                    del self.active_trackers[pos.position_id]

                self.telegram.send_message(
                    f"⏹️ *PAPER POSITION SQUARED OFF ({reason})*\n"
                    f"• Contract: `{pos.symbol}`\n"
                    f"• Exit Price: ₹{res.fill_price:.2f}\n"
                    f"• Net PnL: ₹{pos.realized_pnl:.2f}"
                )

        return results
