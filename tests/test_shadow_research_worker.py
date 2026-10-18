"""Unit tests for Autonomous Background Shadow Research Worker."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.candle_engine import validate_candle
from natras.shadow.background_worker import (
    ShadowResearchWorker,
    is_market_hours,
    is_wifi_or_internet_connected,
)
from natras.shadow.shadow_engine import ShadowEngine

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_market_hours_and_connectivity_detection():
    # Weekday 10:30 IST -> market hours
    t_mkt = datetime(2026, 10, 14, 10, 30, 0, tzinfo=TZ)
    assert is_market_hours(t_mkt) is True

    # Weekday 16:30 IST -> outside market hours
    t_evening = datetime(2026, 10, 14, 16, 30, 0, tzinfo=TZ)
    assert is_market_hours(t_evening) is False

    # Sunday 11:00 IST -> outside market hours
    t_sunday = datetime(2026, 10, 18, 11, 0, 0, tzinfo=TZ)
    assert is_market_hours(t_sunday) is False

    # Connectivity check should execute safely and return bool
    connected = is_wifi_or_internet_connected(timeout=0.5)
    assert isinstance(connected, bool)


def test_live_first_strategy_formulation_and_forward_testing(tmp_path):
    store_path = tmp_path / "shadow_registry.json"
    engine = ShadowEngine(store_path=store_path, auto_seed=False)

    worker = ShadowResearchWorker(
        shadow_engine=engine,
        db_path=tmp_path / "market.db",
        poll_interval_seconds=60.0,
    )

    # 1. Provide live market intelligence with high volatility
    live_intel = {
        "timestamp": "2026-10-15T11:00:00+05:30",
        "regime": {
            "volatility_regime": "HIGH_VOLATILITY",
            "trend_regime": "BULLISH_TRENDING",
        },
        "vix": 18.5,
        "options_intel": {"pcr": 0.95, "max_pain": 24500.0},
        "breadth": {"advance_decline_ratio": 1.45},
    }

    cand = worker.calibrate_candidate_from_live_intelligence(live_intel)
    assert cand is not None
    assert cand.origin == "LIVE_MARKET_DATA"
    assert cand.backtest_status == "PENDING_AUDIT"
    assert "LiveAdaptive_HighVol_Momentum_Wick" in cand.name
    assert cand.candidate_id in engine.active_strategies

    # 2. Test forward directly on incoming live candles first
    t_1105 = datetime(2026, 10, 15, 11, 5, 0, tzinfo=TZ)
    c1 = validate_candle("NSE:NIFTY50-INDEX", t_1105, 24500.0, 24545.0, 24495.0, 24540.0, 150000.0, atr14=18.0)
    engine.on_candle(c1, context=live_intel)

    # Candidate enters virtual forward trade directly
    assert cand.candidate_id in engine.active_positions
    pos = engine.active_positions[cand.candidate_id]
    assert pos["direction"] == "CE"
    assert pos["spot_entry"] == 24540.0


def test_background_audit_with_same_parameters(tmp_path, monkeypatch):
    store_path = tmp_path / "shadow_registry.json"
    engine = ShadowEngine(store_path=store_path, auto_seed=False)

    # Register candidate formulated from live market data
    cand = engine.register_variant(
        name="LiveAdaptive_HighVol_Momentum_Wick",
        param_overrides={"min_body_ratio": 0.50},
        strategy_class_name="RegimeAdaptiveMomentumWickStrategy",
    )
    cand.origin = "LIVE_MARKET_DATA"
    cand.backtest_status = "PENDING_AUDIT"
    engine._save()

    sent_messages = []
    worker = ShadowResearchWorker(
        shadow_engine=engine,
        db_path=Path("data") / "market.db",
        telegram_notifier=lambda msg: sent_messages.append(msg),
    )

    # Run background audit silently
    audited = worker.process_pending_audits(throttled=False)
    assert audited == 1

    # Verify same parameters were backtested and stored on candidate
    assert cand.backtest_status == "AUDITED"
    assert cand.backtest_metrics is not None
    assert "total_trades" in cand.backtest_metrics
    assert "win_rate" in cand.backtest_metrics
    assert "total_net_pnl" in cand.backtest_metrics
    assert cand.backtest_metrics["total_batches"] == 7
