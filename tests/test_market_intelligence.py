"""Unit tests for Phase 4 Market Intelligence system.

Tests:
- SourceHealth and status reporting
- Indian market breadth & sectoral relative strength
- NIFTY options intelligence (PCR, Max Pain)
- Global markets tracking & risk sentiment
- Macroeconomic calendar events
- Market regime detection (Trend, Volatility, Event Risk)
- MarketIntelligenceOrchestrator compilation
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.market_intelligence.economic_events.calendar import EconomicCalendar, EconomicEvent
from natras.market_intelligence.global_market.tracker import GlobalMarketsTracker
from natras.market_intelligence.india.breadth import IndiaMarketBreadthAnalyzer
from natras.market_intelligence.india.options_intel import OptionsIntelligenceAnalyzer
from natras.market_intelligence.orchestrator import MarketIntelligenceOrchestrator
from natras.market_intelligence.regimes.detector import MarketRegimeDetector
from natras.market_intelligence.source_status import SourceHealth, SourceStatus

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_source_health_serialization():
    health = SourceHealth(
        source_name="TestSource",
        status=SourceStatus.ACTIVE,
        category="TEST",
        last_checked="2026-10-14T10:00:00",
        message="All systems nominal",
    )
    d = health.to_dict()
    assert d["status"] == "ACTIVE"
    assert d["source_name"] == "TestSource"


def test_india_market_breadth_analyzer():
    analyzer = IndiaMarketBreadthAnalyzer()
    nifty_quote = {"ltp": 25100.0, "prev_close": 25000.0}  # +0.40%
    sector_quotes = {
        "NSE:NIFTYBANK-INDEX": {"ltp": 52000.0, "prev_close": 51500.0},  # +0.97% (Outperformer)
        "NSE:NIFTYIT-INDEX": {"ltp": 42000.0, "prev_close": 42200.0},    # -0.47% (Underperformer)
        "NSE:NIFTYAUTO-INDEX": {"ltp": 26000.0, "prev_close": 25900.0},
    }

    snap = analyzer.analyze(nifty_quote, sector_quotes)
    assert snap.benchmark_change_pct == 0.40
    assert snap.advances == 2
    assert snap.declines == 1
    assert snap.advance_decline_ratio == 2.0
    assert snap.top_outperforming_sector == "NIFTY_BANK"
    assert snap.health.status == SourceStatus.ACTIVE


def test_options_intelligence_analyzer():
    analyzer = OptionsIntelligenceAnalyzer()
    quotes = [
        {"strike": 24900.0, "type": "CE", "oi": 10000.0, "volume": 5000.0, "ltp": 220.0},
        {"strike": 24900.0, "type": "PE", "oi": 50000.0, "volume": 20000.0, "ltp": 40.0},
        {"strike": 25000.0, "type": "CE", "oi": 80000.0, "volume": 40000.0, "ltp": 120.0},
        {"strike": 25000.0, "type": "PE", "oi": 60000.0, "volume": 35000.0, "ltp": 110.0},
        {"strike": 25100.0, "type": "CE", "oi": 90000.0, "volume": 45000.0, "ltp": 50.0},
        {"strike": 25100.0, "type": "PE", "oi": 15000.0, "volume": 8000.0, "ltp": 230.0},
    ]
    snap = analyzer.analyze(spot=25010.0, expiry="2026-10-15", chain_quotes=quotes)

    assert snap.pcr_oi > 0.0
    assert snap.max_pain_strike in [24900.0, 25000.0, 25100.0]
    assert snap.highest_ce_oi_strike == 25100.0
    assert snap.highest_pe_oi_strike == 25000.0
    assert snap.health.status == SourceStatus.ACTIVE


def test_global_markets_tracker_sentiment():
    tracker = GlobalMarketsTracker()
    manual_quotes = {
        "SP500": {"price": 5850.0, "change_pct": 0.85},
        "NASDAQ100": {"price": 20500.0, "change_pct": 1.20},
        "VIX": {"price": 13.5, "change_pct": -4.5},
        "BRENT_CRUDE": {"price": 78.0, "change_pct": -0.5},
        "USDINR": {"price": 84.10, "change_pct": 0.05},
    }
    snap = tracker.fetch_quotes(manual_quotes=manual_quotes)
    assert snap.risk_sentiment == "RISK_ON"
    assert "SP500" in snap.assets
    assert snap.assets["SP500"].change_pct == 0.85


def test_economic_calendar(tmp_path):
    store_file = tmp_path / "events.json"
    calendar = EconomicCalendar(store_path=store_file)
    events = calendar.get_upcoming_high_impact_events()
    assert len(events) >= 1
    assert any("RBI" in e.headline or "Fed" in e.headline or "CPI" in e.headline for e in events)


def test_market_regime_detector():
    detector = MarketRegimeDetector()

    # Bullish trend + Normal volatility + Risk on sentiment
    breadth_snap = MagicMock()
    breadth_snap.benchmark_change_pct = 0.65
    breadth_snap.advance_decline_ratio = 2.2

    global_snap = MagicMock()
    global_snap.risk_sentiment = "RISK_ON"

    regime = detector.detect(
        breadth=breadth_snap,
        global_snap=global_snap,
        vix_value=14.5,
    )
    assert regime.trend_regime == "BULLISH_TRENDING"
    assert regime.volatility_regime == "NORMAL_VOLATILITY"
    assert regime.global_sentiment == "RISK_ON"
    assert regime.confidence_score >= 0.80


def test_orchestrator_generate_report(tmp_path):
    orchestrator = MarketIntelligenceOrchestrator()
    manual_global = {
        "SP500": {"price": 5800.0, "change_pct": 0.3},
        "VIX": {"price": 14.0, "change_pct": 0.0},
    }
    report = orchestrator.generate_report(manual_global_quotes=manual_global)
    assert report.timestamp is not None
    assert "trend_regime" in report.regime
    assert "volatility_regime" in report.regime
    assert len(report.upcoming_events) >= 1


def test_orchestrator_poll_and_settle_with_db():
    from natras.db.database import DatabaseManager
    db = DatabaseManager(":memory:")
    mock_upstox = MagicMock()
    mock_upstox.is_configured.return_value = True
    mock_upstox.get_pcr.return_value = {"pcr": 1.05}
    mock_upstox.get_max_pain.return_value = {"max_pain": 23500.0}
    mock_upstox.get_fii_activity.return_value = [{"buy_amount": 1000.0, "sell_amount": 800.0}]
    mock_upstox.get_dii_activity.return_value = [{"buy_amount": 2000.0, "sell_amount": 1500.0}]

    orchestrator = MarketIntelligenceOrchestrator(db_manager=db, upstox_client=mock_upstox)
    with patch.object(orchestrator.news_collector, "collect_all", return_value={"new_items_collected": 0, "total_items": 0, "feeds": []}):
        report = orchestrator.poll_and_settle(db)

    assert report.options_intel is not None
    assert report.options_intel["pcr"] == 1.05
    assert report.options_intel["max_pain"] == 23500.0

    # Verify saved in DB
    latest_intel = db.get_latest_market_intelligence()
    assert latest_intel is not None
    assert latest_intel["pcr"] == 1.05
    assert latest_intel["max_pain"] == 23500.0
