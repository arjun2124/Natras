"""Market Intelligence Orchestrator.

Coordinates all Phase 4 market intelligence modules:
1. Indian market breadth & sectoral relative strength
2. NIFTY options market intelligence (PCR, Max Pain, Open Interest)
3. Global markets (US indices, VIX, Crude, Gold, Yields)
4. Macroeconomic calendar (RBI, Fed, CPI)
5. News feeds (RSS + Broker News)
6. Regime detection & context synthesis
7. Persistent settlement into SQLite and runtime JSON stores
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from natras.adapters.upstox_market_data import UpstoxMarketDataClient
from natras.data.session import IST
from natras.db.database import DatabaseManager
from natras.market_intelligence.economic_events.calendar import EconomicCalendar
from natras.market_intelligence.global_market.tracker import GlobalMarketsTracker
from natras.market_intelligence.india.breadth import (
    NIFTY50_CONSTITUENTS,
    SECTOR_SYMBOLS,
    IndiaMarketBreadthAnalyzer,
)
from natras.market_intelligence.india.options_intel import OptionsIntelligenceAnalyzer
from natras.market_intelligence.news.feed import NewsFeedCollector
from natras.market_intelligence.regimes.detector import MarketRegimeDetector

logger = logging.getLogger(__name__)

REPORT_DIR = Path("runtime_data") / "reports" / "market_intelligence"


@dataclass
class MarketIntelligenceReport:
    timestamp: str
    regime: Dict[str, Any]
    breadth: Optional[Dict[str, Any]]
    options_intel: Optional[Dict[str, Any]]
    global_markets: Optional[Dict[str, Any]]
    upcoming_events: list
    recent_news_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketIntelligenceOrchestrator:
    """Central manager for all market intelligence pipelines and settlement."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        upstox_client: Optional[UpstoxMarketDataClient] = None,
        quote_fetcher: Optional[Callable[[Sequence[str]], Dict[str, Any]]] = None,
    ) -> None:
        self.db = db_manager
        self.quote_fetcher = quote_fetcher
        self.upstox = upstox_client or UpstoxMarketDataClient()
        self.breadth_analyzer = IndiaMarketBreadthAnalyzer()
        self.options_analyzer = OptionsIntelligenceAnalyzer()
        self.global_tracker = GlobalMarketsTracker()
        self.calendar = EconomicCalendar()
        self.news_collector = NewsFeedCollector()
        self.regime_detector = MarketRegimeDetector(calendar=self.calendar)

    def generate_report(
        self,
        nifty_quote: Optional[Dict[str, Any]] = None,
        sector_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
        manual_global_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> MarketIntelligenceReport:
        """Run full market intelligence pipeline, compile report, and persist."""
        now_str = datetime.now(IST).isoformat()

        # 1. Breadth
        breadth_snap = None
        constituent_quotes = None
        if (not nifty_quote or not sector_quotes) and self.quote_fetcher:
            try:
                sym_list = ["NSE:NIFTY50-INDEX"] + list(SECTOR_SYMBOLS.values()) + NIFTY50_CONSTITUENTS
                quotes = self.quote_fetcher(sym_list)
                if "NSE:NIFTY50-INDEX" in quotes:
                    nifty_quote = quotes["NSE:NIFTY50-INDEX"]
                sector_quotes = {sym: quotes[sym] for sym in SECTOR_SYMBOLS.values() if sym in quotes}
                constituent_quotes = [quotes[s] for s in NIFTY50_CONSTITUENTS if s in quotes]
            except Exception as exc:
                logger.warning("Quote fetcher failed for breadth analysis: %s", exc)

        if nifty_quote and sector_quotes:
            breadth_snap = self.breadth_analyzer.analyze(
                nifty_quote,
                sector_quotes,
                constituent_quotes=constituent_quotes,
            )

        # 2. Options Intelligence (Live Upstox or Fallback)
        options_intel_dict: Optional[Dict[str, Any]] = None
        if self.upstox and self.upstox.is_configured():
            try:
                pcr_data = self.upstox.get_pcr("NSE_INDEX|Nifty 50")
                pain_data = self.upstox.get_max_pain("NSE_INDEX|Nifty 50")
                fii_data = self.upstox.get_fii_activity("NSE_FO|INDEX_OPTIONS")
                dii_data = self.upstox.get_dii_activity("NSE_EQ|CASH")

                fii_net = 0.0
                if fii_data and isinstance(fii_data, list) and len(fii_data) > 0:
                    latest_fii = fii_data[0]
                    fii_net = float(latest_fii.get("buy_amount", 0.0)) - float(latest_fii.get("sell_amount", 0.0))

                dii_net = 0.0
                if dii_data and isinstance(dii_data, list) and len(dii_data) > 0:
                    latest_dii = dii_data[0]
                    dii_net = float(latest_dii.get("buy_amount", 0.0)) - float(latest_dii.get("sell_amount", 0.0))

                options_intel_dict = {
                    "pcr": pcr_data.get("pcr"),
                    "max_pain": pain_data.get("max_pain"),
                    "fii_net_flow": fii_net,
                    "dii_net_flow": dii_net,
                    "provider": "UPSTOX_LIVE",
                }
            except Exception as exc:
                logger.warning("Upstox live options intel fetch failed: %s", exc)

        # 3. Global markets
        global_snap = self.global_tracker.fetch_quotes(manual_quotes=manual_global_quotes)

        # 4. Regime
        vix_price = 14.0
        if global_snap and "VIX" in global_snap.assets:
            vix_price = global_snap.assets["VIX"].price or 14.0
        regime_snap = self.regime_detector.detect(
            breadth=breadth_snap,
            global_snap=global_snap,
            vix_value=vix_price,
        )

        # 5. Events
        upcoming_events = [e.to_dict() for e in self.calendar.get_upcoming_high_impact_events()]

        # 6. Compile
        report = MarketIntelligenceReport(
            timestamp=now_str,
            regime=regime_snap.to_dict(),
            breadth=breadth_snap.to_dict() if breadth_snap else None,
            options_intel=options_intel_dict,
            global_markets=global_snap.to_dict() if global_snap else None,
            upcoming_events=upcoming_events,
            recent_news_count=len(self.news_collector.items),
        )

        # 7. Settle to disk JSON
        self._save_json(report)

        # 8. Settle to SQLite if DB manager configured
        if self.db:
            self._save_db(report, vix_price)

        return report

    def poll_and_settle(self, db_manager: Optional[DatabaseManager] = None) -> MarketIntelligenceReport:
        """Poll external news/feeds and settle latest intelligence snapshot to persistent storage."""
        target_db = db_manager or self.db

        # 1. Poll news items
        try:
            self.news_collector.collect_all()
            if target_db:
                for item in self.news_collector.items.values():
                    target_db.save_news_item({
                        "news_id": item.news_id,
                        "source": item.source,
                        "url": item.url,
                        "published_at": item.published_at,
                        "category": item.category,
                        "sector": item.sector,
                        "headline": item.headline,
                        "summary": item.summary,
                        "importance": item.importance,
                    })
        except Exception as exc:
            logger.warning("News collection during poll_and_settle failed: %s", exc)

        # 2. Settle economic events
        if target_db:
            try:
                for ev in self.calendar.events.values():
                    target_db.save_economic_event(ev.to_dict())
            except Exception as exc:
                logger.warning("Event settlement failed: %s", exc)

        # 3. Generate snapshot and settle
        old_db = self.db
        self.db = target_db
        try:
            report = self.generate_report()
        finally:
            self.db = old_db
        return report

    def _save_json(self, report: MarketIntelligenceReport) -> None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / "latest.json"
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    def _save_db(self, report: MarketIntelligenceReport, vix: float) -> None:
        if not self.db:
            return
        opt = report.options_intel or {}
        snapshot_payload = {
            "timestamp": report.timestamp,
            "regime": report.regime,
            "pcr": opt.get("pcr"),
            "max_pain": opt.get("max_pain"),
            "vix": vix,
            "fii_net_amount": opt.get("fii_net_flow"),
            "dii_net_amount": opt.get("dii_net_flow"),
            "global_markets": report.global_markets,
            "upcoming_events": report.upcoming_events,
            "market_breadth": report.breadth,
        }
        self.db.save_market_intelligence_snapshot(snapshot_payload)
