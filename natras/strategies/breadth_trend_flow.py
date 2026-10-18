"""Market Breadth & Institutional Capital Flow Trend Strategy.

A structurally distinct shadow strategy built on Macro Breadth and Institutional Flows:
- Multi-EMA trend ribbon (9 EMA x 21 EMA) confirmed by volume surges.
- Gated by Sector Outperformance (Bank & IT relative strength).
- Gated by FII/DII Net Cash and Derivative Flow positivity.
- Completely different indicators, parameters, and entry/exit mechanics.
"""

from __future__ import annotations

import logging
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


class BreadthInstitutionalFlowStrategy(BaseStrategy):
    """Trend following strategy driven by EMA momentum, sector breadth, and institutional flows."""

    STRATEGY_ID = "v2.2.0-SHADOW-BREADTH-INSTITUTIONAL-FLOW"
    VERSION = "1.0.0-FLOW-MOMENTUM"
    CANONICAL_NAME = "Multi-Sector Breadth and Institutional Trend Flow"

    def __init__(
        self,
        strategy_id: str = STRATEGY_ID,
        family: str = STRATEGY_FAMILY,
        fast_ema_period: int = 9,
        slow_ema_period: int = 21,
        volume_ma_period: int = 20,
        volume_surge_multiplier: float = 1.2,
        min_bullish_ad_ratio: float = 1.3,
        max_bearish_ad_ratio: float = 0.7,
        initial_stop_pp: float = 15.0,
        lock_in_trigger_pp: float = 5.0,
        lock_in_level_pp: float = 1.5,
        trail_trigger_pp: float = 9.0,
        trail_gap_pp: float = 3.0,
    ) -> None:
        super().__init__(strategy_id=strategy_id, family=family)
        self.tz = ZoneInfo(MARKET_TIMEZONE_STR)
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.volume_ma_period = volume_ma_period
        self.volume_surge_multiplier = volume_surge_multiplier
        self.min_bullish_ad_ratio = min_bullish_ad_ratio
        self.max_bearish_ad_ratio = max_bearish_ad_ratio
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

    def _calc_ema(self, values: List[float], period: int) -> float:
        """Compute exponential moving average."""
        if len(values) < period:
            return values[-1]
        k = 2.0 / (period + 1.0)
        ema = sum(values[:period]) / period
        for price in values[period:]:
            ema = (price * k) + (ema * (1.0 - k))
        return ema

    def on_candle(self, candle: Candle, context: Optional[Dict[str, Any]] = None) -> List[Signal]:
        self.history.append(candle)
        min_required = max(self.slow_ema_period + 5, self.volume_ma_period + 5)
        if len(self.history) < min_required:
            return []

        c_time = candle.timestamp.astimezone(self.tz).time()
        if c_time < time(9, 25) or c_time > time(14, 45):
            return []

        closes = [c.close for c in self.history]
        volumes = [c.volume for c in self.history]

        fast_ema = self._calc_ema(closes, self.fast_ema_period)
        slow_ema = self._calc_ema(closes, self.slow_ema_period)
        prev_fast_ema = self._calc_ema(closes[:-1], self.fast_ema_period)
        prev_slow_ema = self._calc_ema(closes[:-1], self.slow_ema_period)

        vol_ma = sum(volumes[-self.volume_ma_period:]) / self.volume_ma_period
        has_volume_surge = candle.volume >= (vol_ma * self.volume_surge_multiplier) if vol_ma > 0 else True

        # Market Intelligence from context
        ad_ratio = None
        top_sector = None
        fii_flow = None
        dii_flow = None
        if context:
            breadth = context.get("breadth") or {}
            ad_ratio = breadth.get("advance_decline_ratio")
            top_sector = breadth.get("top_outperforming_sector")
            opt = context.get("options_intel") or context.get("institutional") or {}
            fii_flow = opt.get("fii_net_flow") or opt.get("fii_net_flow_cr")
            dii_flow = opt.get("dii_net_flow") or opt.get("dii_net_flow_cr")

        # 1. Bullish Institutional Trend Crossover
        is_bullish_crossover = (prev_fast_ema <= prev_slow_ema) and (fast_ema > slow_ema)
        breadth_confirms_bullish = True
        flow_confirms_bullish = True

        if ad_ratio is not None and ad_ratio < self.min_bullish_ad_ratio:
            breadth_confirms_bullish = False
        if top_sector is not None and top_sector not in ("NIFTY_BANK", "NIFTY_IT", "NIFTY_AUTO", "NIFTY_FIN_SERVICE"):
            breadth_confirms_bullish = False
        if fii_flow is not None and dii_flow is not None and (fii_flow + dii_flow) < 0:
            flow_confirms_bullish = False

        if is_bullish_crossover and has_volume_surge and breadth_confirms_bullish and flow_confirms_bullish:
            sig = Signal(
                strategy_id=self.strategy_id,
                symbol=candle.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                intent=OrderIntent.ENTRY,
                quantity=1,
                reason=f"BreadthFlow_Bullish_Crossover (EMA9>{fast_ema:.1f}, VolSurge={has_volume_surge}, Sector={top_sector}, A/D={ad_ratio})",
                metadata={
                    "instrument_type": "CE",
                    "setup_type": "INSTITUTIONAL_BREADTH_EXPANSION",
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "top_sector": top_sector,
                    "ad_ratio": ad_ratio,
                    "fii_flow": fii_flow,
                    "dii_flow": dii_flow,
                },
            )
            return [sig]

        # 2. Bearish Institutional Trend Breakdown
        is_bearish_crossover = (prev_fast_ema >= prev_slow_ema) and (fast_ema < slow_ema)
        breadth_confirms_bearish = True
        flow_confirms_bearish = True

        if ad_ratio is not None and ad_ratio > self.max_bearish_ad_ratio:
            breadth_confirms_bearish = False
        if fii_flow is not None and fii_flow > 0:
            flow_confirms_bearish = False

        if is_bearish_crossover and has_volume_surge and breadth_confirms_bearish and flow_confirms_bearish:
            sig = Signal(
                strategy_id=self.strategy_id,
                symbol=candle.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                intent=OrderIntent.ENTRY,
                quantity=1,
                reason=f"BreadthFlow_Bearish_Breakdown (EMA9<{fast_ema:.1f}, VolSurge={has_volume_surge}, A/D={ad_ratio})",
                metadata={
                    "instrument_type": "PE",
                    "setup_type": "INSTITUTIONAL_BREADTH_EXPANSION",
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "ad_ratio": ad_ratio,
                    "fii_flow": fii_flow,
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
            "role": "DIVERSE_INSTITUTIONAL_BREADTH",
            "parameter_defaults": {
                "fast_ema_period": self.fast_ema_period,
                "slow_ema_period": self.slow_ema_period,
                "volume_ma_period": self.volume_ma_period,
                "volume_surge_multiplier": self.volume_surge_multiplier,
                "min_bullish_ad_ratio": self.min_bullish_ad_ratio,
                "max_bearish_ad_ratio": self.max_bearish_ad_ratio,
                "initial_stop_pp": self.initial_stop_pp,
            },
        }
