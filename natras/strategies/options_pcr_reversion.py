"""Options Intelligence & Max Pain Mean-Reversion Strategy.

A structurally distinct shadow strategy built entirely on Options Intelligence:
- Replaces wick momentum with Statistical Mean Reversion (Bollinger Bands + RSI).
- Trades the gravitational pull toward Options Max Pain and PCR sentiment extremes.
- Completely different indicators, parameters, and entry/exit mechanics.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, time
from typing import Any, Dict, List, Optional
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
from natras.strategies.momentum_wick import SteppedTrailingStop

logger = logging.getLogger(__name__)


class OptionsMaxPainPCRReversionStrategy(BaseStrategy):
    """Mean reversion strategy utilizing Options Max Pain, PCR extremes, and Bollinger Bands."""

    STRATEGY_ID = "v2.2.0-SHADOW-OPTIONS-PCR-REVERSION"
    VERSION = "1.0.0-OPTIONS-FLOW"
    CANONICAL_NAME = "Options Max Pain and PCR Mean-Reversion"

    def __init__(
        self,
        strategy_id: str = STRATEGY_ID,
        family: str = STRATEGY_FAMILY,
        bb_period: int = 20,
        bb_std_dev: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        min_max_pain_distance_pts: float = 40.0,
        pcr_oversold_threshold: float = 0.80,
        pcr_overbought_threshold: float = 1.25,
        initial_stop_pp: float = 12.0,
        lock_in_trigger_pp: float = 4.0,
        lock_in_level_pp: float = 1.0,
        trail_trigger_pp: float = 7.0,
        trail_gap_pp: float = 2.5,
    ) -> None:
        super().__init__(strategy_id=strategy_id, family=family)
        self.tz = ZoneInfo(MARKET_TIMEZONE_STR)
        self.bb_period = bb_period
        self.bb_std_dev = bb_std_dev
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.min_max_pain_distance_pts = min_max_pain_distance_pts
        self.pcr_oversold_threshold = pcr_oversold_threshold
        self.pcr_overbought_threshold = pcr_overbought_threshold
        self.initial_stop_pp = initial_stop_pp
        self.lock_in_trigger_pp = lock_in_trigger_pp
        self.lock_in_level_pp = lock_in_level_pp
        self.trail_trigger_pp = trail_trigger_pp
        self.trail_gap_pp = trail_gap_pp
        self.status = StrategyStatus.SHADOW
        self.history: List[Candle] = []

    def is_eod_square_off_time(self, dt: datetime) -> bool:
        t = dt.astimezone(self.tz).time()
        return t >= time(15, 0)

    def create_trailing_stop(self, context: Optional[Dict[str, Any]] = None) -> SteppedTrailingStop:
        return SteppedTrailingStop(
            initial_stop_pp=self.initial_stop_pp,
            lock_in_trigger_pp=self.lock_in_trigger_pp,
            lock_in_level_pp=self.lock_in_level_pp,
            trail_trigger_pp=self.trail_trigger_pp,
            trail_gap_pp=self.trail_gap_pp,
        )

    def _calc_bollinger_bands(self, closes: List[float]) -> Tuple[float, float, float]:
        """Compute (Middle SMA, Upper Band, Lower Band)."""
        window = closes[-self.bb_period:]
        sma = sum(window) / len(window)
        variance = sum((x - sma) ** 2 for x in window) / len(window)
        std = math.sqrt(variance)
        return sma, sma + self.bb_std_dev * std, sma - self.bb_std_dev * std

    def _calc_rsi(self, closes: List[float]) -> float:
        """Compute RSI(14)."""
        if len(closes) < self.rsi_period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(-self.rsi_period, 0):
            diff = closes[i] - closes[i - 1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        avg_gain = sum(gains) / len(gains) if gains else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_candle(self, candle: Candle, context: Optional[Dict[str, Any]] = None) -> List[Signal]:
        self.history.append(candle)
        if len(self.history) < max(self.bb_period + 2, self.rsi_period + 2):
            return []

        c_time = candle.timestamp.astimezone(self.tz).time()
        if c_time < time(9, 25) or c_time > time(14, 45):
            return []

        closes = [c.close for c in self.history]
        mid_bb, upper_bb, lower_bb = self._calc_bollinger_bands(closes)
        rsi = self._calc_rsi(closes)

        # Options Intelligence from context
        pcr = None
        max_pain = None
        if context:
            opt = context.get("options_intel") or context.get("options") or {}
            pcr = opt.get("pcr") if isinstance(opt, dict) else None
            max_pain = opt.get("max_pain") if isinstance(opt, dict) else None
            if pcr is None and "pcr" in context:
                pcr = context["pcr"]
            if max_pain is None and "max_pain" in context:
                max_pain = context["max_pain"]

        spot = candle.close

        # 1. Bullish Mean Reversion Signal (Oversold -> Target Max Pain)
        is_oversold_bb = spot <= lower_bb
        is_oversold_rsi = rsi <= self.rsi_oversold
        options_confirm_bullish = False

        if pcr is not None and pcr <= self.pcr_oversold_threshold:
            options_confirm_bullish = True
        if max_pain is not None and (max_pain - spot) >= self.min_max_pain_distance_pts:
            options_confirm_bullish = True

        # Require technical oversold + options alignment (or strong technical extreme if no context)
        if (is_oversold_bb and is_oversold_rsi) and (options_confirm_bullish or context is None):
            sig = Signal(
                strategy_id=self.strategy_id,
                symbol=candle.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                intent=OrderIntent.ENTRY,
                quantity=1,
                reason=f"OptionsReversion_Oversold_Bounce (RSI={rsi:.1f}, PCR={pcr}, MaxPain={max_pain})",
                metadata={
                    "instrument_type": "CE",
                    "setup_type": "OVERSOLD_REVERSION",
                    "rsi": rsi,
                    "pcr": pcr,
                    "max_pain": max_pain,
                    "mid_bb": mid_bb,
                },
            )
            return [sig]

        # 2. Bearish Mean Reversion Signal (Overbought -> Target Max Pain)
        is_overbought_bb = spot >= upper_bb
        is_overbought_rsi = rsi >= self.rsi_overbought
        options_confirm_bearish = False

        if pcr is not None and pcr >= self.pcr_overbought_threshold:
            options_confirm_bearish = True
        if max_pain is not None and (spot - max_pain) >= self.min_max_pain_distance_pts:
            options_confirm_bearish = True

        if (is_overbought_bb and is_overbought_rsi) and (options_confirm_bearish or context is None):
            sig = Signal(
                strategy_id=self.strategy_id,
                symbol=candle.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                intent=OrderIntent.ENTRY,
                quantity=1,
                reason=f"OptionsReversion_Overbought_Pullback (RSI={rsi:.1f}, PCR={pcr}, MaxPain={max_pain})",
                metadata={
                    "instrument_type": "PE",
                    "setup_type": "OVERBOUGHT_REVERSION",
                    "rsi": rsi,
                    "pcr": pcr,
                    "max_pain": max_pain,
                    "mid_bb": mid_bb,
                },
            )
            return [sig]

        return []

    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "canonical_name": self.CANONICAL_NAME,
            "version": self.VERSION,
            "family": self.family,
            "status": self.status.value,
            "role": "DIVERSE_OPTIONS_GRAVITY",
            "parameter_defaults": {
                "bb_period": self.bb_period,
                "bb_std_dev": self.bb_std_dev,
                "rsi_period": self.rsi_period,
                "rsi_oversold": self.rsi_oversold,
                "rsi_overbought": self.rsi_overbought,
                "min_max_pain_distance_pts": self.min_max_pain_distance_pts,
                "pcr_oversold_threshold": self.pcr_oversold_threshold,
                "pcr_overbought_threshold": self.pcr_overbought_threshold,
                "initial_stop_pp": self.initial_stop_pp,
            },
        }
