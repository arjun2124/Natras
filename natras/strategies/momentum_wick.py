"""Implementation of v2.2.0-RESEARCH-MOMENTUM-WICK strategy.

Canonical Name: Intraday Bar and Wick Momentum
Code Aliases: Momentum_Wick, LiveSniperBot, Sniper_Strategy
Version: 2.2.0

Rules:
1. Evaluates completed 5-minute spot candles.
2. Candle range >= 1.0 * ATR(14).
3. Baseline body-to-range ratio >= 0.45; high-conviction variant >= 0.65.
4. Direction:
   - Bullish candle (close > open) -> Buy Call Option (CE).
   - Bearish candle (close < open) -> Buy Put Option (PE).
5. Initial hard stop in option premium: -15.0 PP.
6. Dynamic Stepped / Trailing Stop:
   - When peak favorable excursion >= +4.0 PP: lock stop at +1.0 PP.
   - When peak favorable excursion >= +8.0 PP: trail stop by 3.0 PP (peak_fav - 3.0 PP).
   - Trailing stop never loosens.
7. Verified Profit Target: None (managed purely by stepped trailing stop).
8. Trading Session Window: 09:20 to 14:45 IST; forced EOD square-off at 15:00 / 15:15 IST.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from natras.config.constants import (
    MARKET_TIMEZONE_STR,
    STRATEGY_FAMILY,
    OrderIntent,
    OrderSide,
    OrderType,
    StrategyStatus,
)
from natras.data.candle_engine import Candle
from natras.strategies.base import BaseStrategy, Signal


@dataclass
class SteppedTrailingStop:
    """Dynamic stepped trailing stop tracker operating on option premium points (PP).

    Rules:
    - Initial stop: -15.0 PP
    - Peak fav >= +4.0 PP: Lock stop at +1.0 PP
    - Peak fav >= +8.0 PP: Trail stop at peak - 3.0 PP
    - Invariant: Trailing stop ratchets upward only (never loosens).
    """

    initial_stop_pp: float = 15.0
    lock_in_trigger_pp: float = 4.0
    lock_in_level_pp: float = 1.0
    trail_trigger_pp: float = 8.0
    trail_gap_pp: float = 3.0

    current_stop_pp: float = field(init=False)
    peak_fav_pp: float = 0.0

    def __post_init__(self) -> None:
        self.current_stop_pp = -abs(self.initial_stop_pp)
        self.peak_fav_pp = 0.0

    def update(self, current_fav_pp: float) -> float:
        """Update peak favorable excursion and ratchet stop level upward.

        Returns:
            The updated current_stop_pp level.
        """
        if current_fav_pp > self.peak_fav_pp:
            self.peak_fav_pp = current_fav_pp

        calculated_stop = -abs(self.initial_stop_pp)

        # Tier 2: Trail trigger (>= +8.0 PP)
        if self.peak_fav_pp >= self.trail_trigger_pp:
            calculated_stop = self.peak_fav_pp - self.trail_gap_pp
        # Tier 1: Lock-in trigger (>= +4.0 PP)
        elif self.peak_fav_pp >= self.lock_in_trigger_pp:
            calculated_stop = self.lock_in_level_pp

        # Invariant: Never loosen the stop
        if calculated_stop > self.current_stop_pp:
            self.current_stop_pp = calculated_stop

        return self.current_stop_pp

    def is_stopped_out(self, current_pnl_pp: float) -> bool:
        """Check if current option PnL in points breaches the current stop level."""
        return current_pnl_pp <= self.current_stop_pp


class MomentumWickStrategy(BaseStrategy):
    """Intraday Bar and Wick Momentum Strategy (v2.2.0-RESEARCH-MOMENTUM-WICK)."""

    STRATEGY_ID = "v2.2.0-RESEARCH-MOMENTUM-WICK"
    VERSION = "2.2.0"
    CANONICAL_NAME = "Intraday Bar and Wick Momentum"
    ALIASES = ["Momentum_Wick", "LiveSniperBot", "Sniper_Strategy"]

    def __init__(
        self,
        strategy_id: str = STRATEGY_ID,
        timeframe_min: int = 5,
        atr_period: int = 14,
        min_range_atr_ratio: float = 1.0,
        min_body_ratio: float = 0.45,
        high_conviction_body_ratio: float = 0.65,
        initial_stop_pp: float = 15.0,
        lock_in_trigger_pp: float = 4.0,
        lock_in_level_pp: float = 1.0,
        trail_trigger_pp: float = 8.0,
        trail_gap_pp: float = 3.0,
        fixed_target_pp: Optional[float] = None,
        lots: int = 1,
        entry_start_time: time = time(9, 20),
        no_new_entries_time: time = time(14, 45),
        eod_square_off_time: time = time(15, 0),
        allow_multiple_positions: bool = True,
        max_concurrent_positions: int = 999999,
    ) -> None:
        super().__init__(strategy_id=strategy_id, family=STRATEGY_FAMILY)
        self.version = self.VERSION
        self.canonical_name = self.CANONICAL_NAME
        self.aliases = list(self.ALIASES)
        self.status = StrategyStatus.ACTIVE_PAPER

        # Core Parameters
        self.timeframe_min = timeframe_min
        self.atr_period = atr_period
        self.min_range_atr_ratio = min_range_atr_ratio
        self.min_body_ratio = min_body_ratio
        self.high_conviction_body_ratio = high_conviction_body_ratio
        self.initial_stop_pp = initial_stop_pp
        self.lock_in_trigger_pp = lock_in_trigger_pp
        self.lock_in_level_pp = lock_in_level_pp
        self.trail_trigger_pp = trail_trigger_pp
        self.trail_gap_pp = trail_gap_pp
        self.fixed_target_pp = fixed_target_pp
        self.lots = lots
        self.entry_start_time = entry_start_time
        self.no_new_entries_time = no_new_entries_time
        self.eod_square_off_time = eod_square_off_time
        self.allow_multiple_positions = allow_multiple_positions
        self.max_concurrent_positions = max_concurrent_positions

        # Internal State
        self.history: List[Candle] = []
        self.last_rejection_reason: Optional[str] = None
        self.tz = ZoneInfo(MARKET_TIMEZONE_STR)

    def resolve_atr(self, candle: Candle, context: Optional[Any] = None) -> Tuple[float, str]:
        """Resolve ATR(14) using the strict 5-tier precedence hierarchy.

        Precedence:
        1. candle.atr14 if present and > 0
        2. candle.atr if present and > 0
        3. context.market_metrics["atr14"] if present and > 0
        4. rolling TR/ATR approximation from recent closed 5m candles (if >= 14 candles in history)
        5. fallback 15.0 only if needed to preserve supplied behavior (explicitly documented)
        """
        # Tier 1: candle.atr14
        atr14_val = getattr(candle, "atr14", None)
        if atr14_val is not None and atr14_val > 0:
            return float(atr14_val), "candle.atr14"

        # Tier 2: candle.atr
        atr_val = getattr(candle, "atr", None)
        if atr_val is not None and atr_val > 0:
            return float(atr_val), "candle.atr"

        # Tier 3: context.market_metrics["atr14"]
        if context is not None:
            metrics = None
            if isinstance(context, dict):
                metrics = context.get("market_metrics")
            elif hasattr(context, "market_metrics"):
                metrics = getattr(context, "market_metrics")

            if isinstance(metrics, dict):
                ctx_atr = metrics.get("atr14")
                if ctx_atr is not None and ctx_atr > 0:
                    return float(ctx_atr), "context.market_metrics['atr14']"

        # Tier 4: Rolling TR/ATR approximation from closed 5m candles
        if len(self.history) >= self.atr_period:
            # Calculate True Range for the last N candles
            tr_values: List[float] = []
            for i in range(len(self.history)):
                curr = self.history[i]
                if i == 0:
                    tr = curr.high - curr.low
                else:
                    prev_c = self.history[i - 1].close
                    tr = max(curr.high - curr.low, abs(curr.high - prev_c), abs(curr.low - prev_c))
                tr_values.append(tr)

            recent_trs = tr_values[-self.atr_period :]
            rolling_atr = sum(recent_trs) / len(recent_trs)
            if rolling_atr > 0:
                return float(rolling_atr), "rolling_tr_approximation"

        # Tier 5: Fallback default (explicitly documented as supplied baseline behavior)
        return 15.0, "fallback_default_15.0"

    def create_trailing_stop(self) -> SteppedTrailingStop:
        """Factory for a new SteppedTrailingStop instance matching strategy parameters."""
        return SteppedTrailingStop(
            initial_stop_pp=self.initial_stop_pp,
            lock_in_trigger_pp=self.lock_in_trigger_pp,
            lock_in_level_pp=self.lock_in_level_pp,
            trail_trigger_pp=self.trail_trigger_pp,
            trail_gap_pp=self.trail_gap_pp,
        )

    def on_tick(self, tick: Any) -> List[Signal]:
        """Preserve reference behavior: entries are strictly prohibited from on_tick."""
        return []

    def on_candle(self, candle: Candle, context: Optional[Any] = None) -> List[Signal]:
        """Process completed 5-minute spot candle and produce trading signals."""
        self.last_rejection_reason = None

        # 1. Timeframe Check (Completed 5-minute candles only)
        if candle.timeframe != self.timeframe_min:
            self.last_rejection_reason = (
                f"Timeframe mismatch: candle timeframe ({candle.timeframe}m) != required ({self.timeframe_min}m)."
            )
            return []

        # 2. Localize to IST
        if candle.timestamp.tzinfo is None:
            c_time = candle.timestamp.replace(tzinfo=self.tz).time()
        else:
            c_time = candle.timestamp.astimezone(self.tz).time()

        # 3. Trading Session Window Check (09:20 to 14:45 IST)
        if c_time < self.entry_start_time:
            self.last_rejection_reason = (
                f"Trading window: time {c_time.strftime('%H:%M')} is before entry start {self.entry_start_time.strftime('%H:%M')} IST."
            )
            self.history.append(candle)
            return []

        if c_time > self.no_new_entries_time:
            self.last_rejection_reason = (
                f"Trading window: time {c_time.strftime('%H:%M')} is after entry cutoff {self.no_new_entries_time.strftime('%H:%M')} IST."
            )
            self.history.append(candle)
            return []

        # 4. Doji Rejection
        candle_range = candle.high - candle.low
        if candle_range <= 0 or candle.close == candle.open:
            self.last_rejection_reason = (
                f"Doji candle: close ({candle.close}) equals open ({candle.open}) or range ({candle_range}) is zero."
            )
            self.history.append(candle)
            return []

        # 5. ATR Precedence & Range Threshold Check (Range >= 1.0 * ATR)
        atr, atr_source = self.resolve_atr(candle, context)
        min_required_range = self.min_range_atr_ratio * atr
        if candle_range < min_required_range:
            self.last_rejection_reason = (
                f"ATR range threshold failed: candle range ({candle_range:.2f}) < "
                f"{self.min_range_atr_ratio} * ATR ({atr:.2f}) [{atr_source}]."
            )
            self.history.append(candle)
            return []

        # 6. Body-to-Range Ratio Check (>= 0.45)
        body = abs(candle.close - candle.open)
        body_ratio = body / candle_range
        if body_ratio < self.min_body_ratio:
            self.last_rejection_reason = (
                f"Body-to-range ratio failed: body ratio ({body_ratio:.3f}) < "
                f"minimum threshold ({self.min_body_ratio})."
            )
            self.history.append(candle)
            return []

        # 7. Variant Classification
        if body_ratio >= self.high_conviction_body_ratio:
            variant = "HIGH_CONVICTION"
        else:
            variant = "BASELINE"

        # 8. Direction & Option Instrument Type
        is_bullish = candle.close > candle.open
        if is_bullish:
            instrument_type = "CE"
            technical_sl = candle.low
            direction_name = "Bullish"
        else:
            instrument_type = "PE"
            technical_sl = candle.high
            direction_name = "Bearish"

        # Record candle in internal history
        self.history.append(candle)

        # 9. Signal Generation
        signal = Signal(
            strategy_id=self.strategy_id,
            symbol=candle.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            intent=OrderIntent.ENTRY,
            quantity=self.lots,
            target_price=self.fixed_target_pp,  # None
            stop_loss=None,  # Hard stop managed in option premium points (-15.0 PP)
            take_profit=None,  # No fixed profit target
            reason=f"{direction_name} momentum wick ({variant}) - {instrument_type} entry",
            metadata={
                "variant": variant,
                "instrument_type": instrument_type,
                "technical_sl": technical_sl,
                "spot_close": candle.close,
                "candle_open": candle.open,
                "candle_high": candle.high,
                "candle_low": candle.low,
                "candle_range": candle_range,
                "body": body,
                "body_ratio": round(body_ratio, 4),
                "atr": round(atr, 4),
                "atr_source": atr_source,
                "initial_stop_pp": self.initial_stop_pp,
                "lock_in_trigger_pp": self.lock_in_trigger_pp,
                "lock_in_level_pp": self.lock_in_level_pp,
                "trail_trigger_pp": self.trail_trigger_pp,
                "trail_gap_pp": self.trail_gap_pp,
                "fixed_target_pp": self.fixed_target_pp,
                "trading_session_window": "09:20-14:45 IST",
                "forced_eod_square_off": "15:00 IST",
            },
        )

        return [signal]

    def is_eod_square_off_time(self, current_dt: datetime) -> bool:
        """Check if current time has reached or passed the EOD square-off cutoff."""
        if current_dt.tzinfo is None:
            c_time = current_dt.replace(tzinfo=self.tz).time()
        else:
            c_time = current_dt.astimezone(self.tz).time()
        return c_time >= self.eod_square_off_time

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status and operational metrics."""
        return {
            "strategy_id": self.strategy_id,
            "canonical_name": self.canonical_name,
            "version": self.version,
            "family": self.family,
            "status": self.status.value,
            "message": f"Confirmed baseline: {self.canonical_name} (v{self.version}) active for paper trading.",
            "aliases": self.aliases,
            "timeframe_min": self.timeframe_min,
            "history_candles": len(self.history),
            "last_rejection_reason": self.last_rejection_reason,
            "parameter_defaults": {
                "timeframe_min": self.timeframe_min,
                "atr_period": self.atr_period,
                "min_range_atr_ratio": self.min_range_atr_ratio,
                "min_body_ratio": self.min_body_ratio,
                "high_conviction_body_ratio": self.high_conviction_body_ratio,
                "initial_stop_pp": self.initial_stop_pp,
                "lock_in_trigger_pp": self.lock_in_trigger_pp,
                "lock_in_level_pp": self.lock_in_level_pp,
                "trail_trigger_pp": self.trail_trigger_pp,
                "trail_gap_pp": self.trail_gap_pp,
                "fixed_target_pp": self.fixed_target_pp,
                "lots": self.lots,
                "entry_start_time": self.entry_start_time.strftime("%H:%M"),
                "no_new_entries_time": self.no_new_entries_time.strftime("%H:%M"),
                "eod_square_off_time": self.eod_square_off_time.strftime("%H:%M"),
                "allow_multiple_positions": self.allow_multiple_positions,
                "max_concurrent_positions": self.max_concurrent_positions,
            },
        }
