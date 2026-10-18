"""Market regime detection and context synthesis engine.

Implements Section 12 (Market regimes) of the NAT RAS Master Project Draft:
Classifies market state into:
- Trend Regime: BULLISH_TRENDING, BEARISH_TRENDING, CHOPPY_SIDEWAYS
- Volatility Regime: LOW_VOLATILITY, NORMAL_VOLATILITY, HIGH_VOLATILITY, EXTREME_VOLATILITY
- Global Macro Sentiment: RISK_ON, RISK_OFF, NEUTRAL
- Event Risk: NORMAL_DAY, EVENT_DAY (Central bank decisions, CPI releases)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from natras.data.session import IST
from natras.market_intelligence.economic_events.calendar import EconomicCalendar
from natras.market_intelligence.global_market.tracker import GlobalMarketSnapshot
from natras.market_intelligence.india.breadth import MarketBreadthSnapshot


@dataclass
class MarketRegimeSnapshot:
    timestamp: str
    trend_regime: str  # BULLISH_TRENDING, BEARISH_TRENDING, CHOPPY_SIDEWAYS
    volatility_regime: str  # LOW_VOLATILITY, NORMAL_VOLATILITY, HIGH_VOLATILITY, EXTREME_VOLATILITY
    global_sentiment: str  # RISK_ON, RISK_OFF, NEUTRAL
    is_event_day: bool
    event_names: List[str]
    advance_decline_ratio: float
    confidence_score: float  # 0.0 to 1.0
    summary_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketRegimeDetector:
    """Synthesizes Indian breadth, global markets, and calendar events into a cohesive regime state."""

    def __init__(self, calendar: Optional[EconomicCalendar] = None) -> None:
        self.calendar = calendar or EconomicCalendar()

    def detect(
        self,
        breadth: Optional[MarketBreadthSnapshot] = None,
        global_snap: Optional[GlobalMarketSnapshot] = None,
        vix_value: float = 14.0,
        current_dt: Optional[datetime] = None,
    ) -> MarketRegimeSnapshot:
        """Synthesize overall market regime."""
        now = current_dt or datetime.now(IST)
        date_str = now.date().isoformat()
        now_str = now.isoformat()

        # 1. Volatility Regime based on VIX
        if vix_value < 12.0:
            vol_regime = "LOW_VOLATILITY"
        elif vix_value <= 17.0:
            vol_regime = "NORMAL_VOLATILITY"
        elif vix_value <= 23.0:
            vol_regime = "HIGH_VOLATILITY"
        else:
            vol_regime = "EXTREME_VOLATILITY"

        # 2. Trend Regime based on Breadth & Benchmark return
        ad_ratio = breadth.advance_decline_ratio if breadth else 1.0
        nifty_chg = breadth.benchmark_change_pct if breadth else 0.0

        if nifty_chg > 0.4 and ad_ratio >= 1.5:
            trend_regime = "BULLISH_TRENDING"
        elif nifty_chg < -0.4 and ad_ratio <= 0.65:
            trend_regime = "BEARISH_TRENDING"
        else:
            trend_regime = "CHOPPY_SIDEWAYS"

        # 3. Global Sentiment
        global_sent = global_snap.risk_sentiment if global_snap else "NEUTRAL"

        # 4. Event Risk
        is_ev, ev_list = self.calendar.is_event_day(date_str)
        event_names = [e.headline for e in ev_list]

        # 5. Regime Confidence & Alignment
        # If trend and global sentiment align, confidence is high (0.85+)
        aligned = (
            (trend_regime == "BULLISH_TRENDING" and global_sent == "RISK_ON") or
            (trend_regime == "BEARISH_TRENDING" and global_sent == "RISK_OFF")
        )
        if aligned:
            confidence = 0.90
        elif trend_regime == "CHOPPY_SIDEWAYS" or global_sent == "NEUTRAL":
            confidence = 0.60
        else:
            # Divergence (e.g. Domestic bull vs Global risk off)
            confidence = 0.45

        summary = (
            f"Market in {trend_regime} state ({vol_regime}). "
            f"Global sentiment is {global_sent}. "
            f"Event Risk: {'HIGH (' + ', '.join(event_names[:2]) + ')' if is_ev else 'NORMAL'}. "
            f"A/D Ratio: {ad_ratio:.2f}."
        )

        return MarketRegimeSnapshot(
            timestamp=now_str,
            trend_regime=trend_regime,
            volatility_regime=vol_regime,
            global_sentiment=global_sent,
            is_event_day=is_ev,
            event_names=event_names,
            advance_decline_ratio=ad_ratio,
            confidence_score=confidence,
            summary_text=summary,
        )
