"""Regime-Adaptive Momentum Wick Strategy (Direct Enhancer of Baseline).

Enhances v2.2.0-RESEARCH-MOMENTUM-WICK by ingesting Market Intelligence:
- Gated by Trend Regime (suppresses counter-trend wicks against prevailing regime).
- Validated by Advance/Decline breadth ratio.
- Dynamically tightens Stepped Trailing Stop during HIGH_VOLATILITY or elevated India VIX.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from natras.strategies.momentum_wick import MomentumWickStrategy, SteppedTrailingStop

logger = logging.getLogger(__name__)


class RegimeAdaptiveMomentumWickStrategy(BaseStrategy):
    """Direct enhancer of baseline Momentum Wick with Market Intelligence gating."""

    STRATEGY_ID = "v2.2.0-SHADOW-REGIME-MOMENTUM-WICK"
    VERSION = "2.2.0-MI-ENHANCED"
    CANONICAL_NAME = "Regime-Adaptive Intraday Bar and Wick Momentum"

    def __init__(
        self,
        strategy_id: str = STRATEGY_ID,
        family: str = STRATEGY_FAMILY,
        min_body_ratio: float = 0.45,
        min_range_atr_ratio: float = 1.0,
        normal_lock_in_trigger_pp: float = 4.0,
        normal_trail_trigger_pp: float = 8.0,
        normal_trail_gap_pp: float = 3.0,
        high_vol_lock_in_trigger_pp: float = 3.0,
        high_vol_trail_trigger_pp: float = 6.0,
        high_vol_trail_gap_pp: float = 2.0,
        vix_high_threshold: float = 16.0,
    ) -> None:
        super().__init__(strategy_id=strategy_id, family=family)
        self.tz = ZoneInfo(MARKET_TIMEZONE_STR)
        self.base_strategy = MomentumWickStrategy(
            strategy_id=strategy_id,
            min_body_ratio=min_body_ratio,
            min_range_atr_ratio=min_range_atr_ratio,
        )
        self.min_body_ratio = min_body_ratio
        self.min_range_atr_ratio = min_range_atr_ratio
        self.normal_lock_in_trigger_pp = normal_lock_in_trigger_pp
        self.normal_trail_trigger_pp = normal_trail_trigger_pp
        self.normal_trail_gap_pp = normal_trail_gap_pp
        self.high_vol_lock_in_trigger_pp = high_vol_lock_in_trigger_pp
        self.high_vol_trail_trigger_pp = high_vol_trail_trigger_pp
        self.high_vol_trail_gap_pp = high_vol_trail_gap_pp
        self.vix_high_threshold = vix_high_threshold
        self.status = StrategyStatus.SHADOW

    @property
    def history(self) -> List[Candle]:
        return self.base_strategy.history

    @history.setter
    def history(self, candles: List[Candle]) -> None:
        self.base_strategy.history = candles

    def is_eod_square_off_time(self, dt: datetime) -> bool:
        return self.base_strategy.is_eod_square_off_time(dt)

    def create_trailing_stop(self, context: Optional[Dict[str, Any]] = None) -> SteppedTrailingStop:
        """Create dynamically adapted trailing stop based on current volatility regime."""
        is_high_vol = False
        if context:
            regime = context.get("regime") or {}
            vol_regime = regime.get("volatility_regime", "NORMAL_VOLATILITY")
            vix = context.get("vix") or (context.get("global_markets") or {}).get("vix", 14.0)
            if vol_regime == "HIGH_VOLATILITY" or (isinstance(vix, (int, float)) and vix >= self.vix_high_threshold):
                is_high_vol = True

        if is_high_vol:
            return SteppedTrailingStop(
                initial_stop_pp=15.0,
                lock_in_trigger_pp=self.high_vol_lock_in_trigger_pp,
                lock_in_level_pp=1.0,
                trail_trigger_pp=self.high_vol_trail_trigger_pp,
                trail_gap_pp=self.high_vol_trail_gap_pp,
            )
        return SteppedTrailingStop(
            initial_stop_pp=15.0,
            lock_in_trigger_pp=self.normal_lock_in_trigger_pp,
            lock_in_level_pp=1.0,
            trail_trigger_pp=self.normal_trail_trigger_pp,
            trail_gap_pp=self.normal_trail_gap_pp,
        )

    def on_candle(self, candle: Candle, context: Optional[Dict[str, Any]] = None) -> List[Signal]:
        """Evaluate candle with Market Intelligence gating."""
        base_signals = self.base_strategy.on_candle(candle)
        if not base_signals:
            return []

        # If no market intelligence context available, pass through base signal
        if not context:
            return base_signals

        sig = base_signals[0]
        direction = sig.metadata.get("instrument_type", "CE")

        regime = context.get("regime") or {}
        trend_regime = regime.get("trend_regime", "NEUTRAL")
        ad_ratio = regime.get("advance_decline_ratio")
        if ad_ratio is None:
            breadth = context.get("breadth") or {}
            ad_ratio = breadth.get("advance_decline_ratio")

        # 1. Bullish CE Confluence Filter
        if direction == "CE":
            if trend_regime in ("BEARISH_TRENDING", "STRONG_BEARISH"):
                logger.info(
                    "RegimeAdaptive: Suppressed CE signal due to BEARISH_TRENDING regime (%s)",
                    trend_regime,
                )
                return []
            if isinstance(ad_ratio, (int, float)) and ad_ratio < 0.80:
                logger.info(
                    "RegimeAdaptive: Suppressed CE signal due to weak market breadth (A/D=%.2f < 0.80)",
                    ad_ratio,
                )
                return []

        # 2. Bearish PE Confluence Filter
        elif direction == "PE":
            if trend_regime in ("BULLISH_TRENDING", "STRONG_BULLISH"):
                logger.info(
                    "RegimeAdaptive: Suppressed PE signal due to BULLISH_TRENDING regime (%s)",
                    trend_regime,
                )
                return []
            if isinstance(ad_ratio, (int, float)) and ad_ratio > 1.25:
                logger.info(
                    "RegimeAdaptive: Suppressed PE signal due to strong market breadth (A/D=%.2f > 1.25)",
                    ad_ratio,
                )
                return []

        sig.metadata["regime_filtered"] = True
        sig.metadata["trend_regime"] = trend_regime
        return [sig]

    def get_status(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "canonical_name": self.CANONICAL_NAME,
            "version": self.VERSION,
            "family": self.family,
            "status": self.status.value,
            "role": "LIVE_BASELINE_ENHANCER",
            "parameter_defaults": {
                "min_body_ratio": self.min_body_ratio,
                "min_range_atr_ratio": self.min_range_atr_ratio,
                "high_vol_trail_trigger_pp": self.high_vol_trail_trigger_pp,
                "high_vol_trail_gap_pp": self.high_vol_trail_gap_pp,
                "vix_high_threshold": self.vix_high_threshold,
            },
        }
