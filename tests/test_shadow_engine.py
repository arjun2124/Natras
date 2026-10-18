"""Unit tests for ShadowEngine and multi-session candidate lifecycle."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.candle_engine import validate_candle
from natras.shadow.shadow_engine import ShadowEngine

TZ = ZoneInfo(MARKET_TIMEZONE_STR)


def test_shadow_engine_variant_lifecycle(tmp_path):
    store_file = tmp_path / "shadow_registry.json"
    engine = ShadowEngine(store_path=store_file)

    # Register variant with lower body ratio
    candidate = engine.register_variant(
        name="Experimental_Aggressive_Body_0.40",
        param_overrides={"min_body_ratio": 0.40},
    )
    assert candidate.status == "SHADOW_LIVE"
    assert candidate.candidate_id in engine.active_strategies

    # Process candle that triggers virtual trade
    t_1000 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    c1 = validate_candle("NSE:NIFTY50-INDEX", t_1000, 24500.0, 24535.0, 24495.0, 24530.0, 100000.0, atr14=15.0)
    engine.on_candle(c1)
    assert candidate.candidate_id in engine.active_positions

    # Candle reaches EOD square-off
    t_1500 = datetime(2026, 10, 14, 15, 0, 0, tzinfo=TZ)
    c_eod = validate_candle("NSE:NIFTY50-INDEX", t_1500, 24530.0, 24540.0, 24525.0, 24535.0, 50000.0, atr14=15.0)
    engine.on_candle(c_eod)

    assert candidate.candidate_id not in engine.active_positions
    assert candidate.trade_count == 1
    assert len(candidate.trades) == 1


def test_shadow_engine_auto_seed(tmp_path):
    store_file = tmp_path / "shadow_registry.json"
    engine = ShadowEngine(store_path=store_file, auto_seed=True)

    assert len(engine.candidates) == 3
    assert len(engine.active_strategies) == 3
    names = {c.name for c in engine.candidates.values()}
    assert "Candidate_1_Live_Enhancer (Regime_Adaptive_Wick)" in names
    assert "Candidate_2_Options_Gravity (PCR_MaxPain_Reversion)" in names
    assert "Candidate_3_Institutional_Flow (SectorBreadth_EMA)" in names
    assert store_file.exists()


def test_shadow_engine_position_persistence_and_reload(tmp_path):
    store_file = tmp_path / "shadow_registry.json"
    engine1 = ShadowEngine(store_path=store_file, auto_seed=False)
    cand = engine1.register_variant("Test_Variant", {"min_body_ratio": 0.40})

    # Trigger a trade entry
    t_1000 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    c1 = validate_candle("NSE:NIFTY50-INDEX", t_1000, 24500.0, 24535.0, 24495.0, 24530.0, 100000.0, atr14=15.0)
    engine1.on_candle(c1)
    assert cand.candidate_id in engine1.active_positions
    assert cand.active_position is not None

    # Re-instantiate engine from store_file (simulating restart)
    engine2 = ShadowEngine(store_path=store_file, auto_seed=False)
    assert cand.candidate_id in engine2.active_positions
    pos2 = engine2.active_positions[cand.candidate_id]
    assert pos2["direction"] == "CE"
    assert pos2["spot_entry"] == 24530.0
    assert hasattr(pos2["trailing_stop"], "update")


def test_shadow_engine_paper_runtime_integration(tmp_path):
    from natras.execution.paper_runtime import PaperRuntime
    from natras.db.database import DatabaseManager

    db_path = tmp_path / "test_natras.db"
    db = DatabaseManager(str(db_path))
    shadow_store = tmp_path / "shadow_registry.json"
    shadow_engine = ShadowEngine(store_path=shadow_store, auto_seed=True)

    runtime = PaperRuntime(
        db=db,
        shadow_engine=shadow_engine,
    )
    assert runtime.shadow_engine is not None
    assert len(runtime.shadow_engine.candidates) == 3

    t_1000 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    c1 = validate_candle("NSE:NIFTY50-INDEX", t_1000, 24500.0, 24535.0, 24495.0, 24530.0, 100000.0, atr14=15.0)
    runtime.on_spot_candle(c1)

    # Verify at least one candidate entered virtual position
    active_in_shadow = runtime.shadow_engine.active_positions
    assert len(active_in_shadow) >= 1


def test_shadow_telegram_dispatch(tmp_path):
    from natras.adapters.telegram_live import TelegramLiveBot
    from natras.db.database import DatabaseManager

    db_path = tmp_path / "test_natras.db"
    db = DatabaseManager(str(db_path))
    bot = TelegramLiveBot(db_manager=db)

    resp = bot.handle_command("/shadow_status")
    assert "SHADOW STRATEGIES" in resp

    resp2 = bot.handle_command("/shadow_candidates")
    assert "SHADOW STRATEGIES" in resp2

    resp3 = bot.handle_command("/shadow_audit")
    assert "SHADOW STRATEGIES WALK-FORWARD AUDIT" in resp3


def test_shadow_regime_adaptive_market_intelligence_gating(tmp_path):
    from natras.strategies.regime_momentum_wick import RegimeAdaptiveMomentumWickStrategy

    strat = RegimeAdaptiveMomentumWickStrategy()
    t_1000 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    # Candle that would produce CE signal under baseline rules
    c1 = validate_candle("NSE:NIFTY50-INDEX", t_1000, 24500.0, 24535.0, 24495.0, 24530.0, 100000.0, atr14=15.0)

    # 1. Bearish trend regime context -> must suppress CE signal
    bear_context = {
        "regime": {"trend_regime": "BEARISH_TRENDING", "advance_decline_ratio": 0.50},
    }
    signals_bear = strat.on_candle(c1, context=bear_context)
    assert len(signals_bear) == 0

    # 2. Bullish trend regime context -> must allow CE signal
    bull_context = {
        "regime": {"trend_regime": "BULLISH_TRENDING", "advance_decline_ratio": 1.50},
    }
    strat.history.clear()
    signals_bull = strat.on_candle(c1, context=bull_context)
    assert len(signals_bull) == 1
    assert signals_bull[0].metadata["instrument_type"] == "CE"
    assert signals_bull[0].metadata["regime_filtered"] is True


def test_shadow_options_pcr_reversion_strategy():
    from natras.strategies.options_pcr_reversion import OptionsMaxPainPCRReversionStrategy

    strat = OptionsMaxPainPCRReversionStrategy(rsi_period=5, bb_period=5, bb_std_dev=1.0)
    t = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    prices = [24500.0, 24500.0, 24500.0, 24500.0, 24500.0, 24500.0]
    for i, p in enumerate(prices):
        c = validate_candle("NSE:NIFTY50-INDEX", t + timedelta(minutes=5 * i), p, p + 5, p - 5, p, 100000.0)
        strat.history.append(c)

    # With extreme oversold PCR (0.65) and spot far below Max Pain (24500)
    context = {
        "options": {"pcr": 0.65, "max_pain": 24500.0},
    }
    eval_c = validate_candle("NSE:NIFTY50-INDEX", t + timedelta(minutes=30), 24500.0, 24500.0, 24190.0, 24200.0, 100000.0)
    signals = strat.on_candle(eval_c, context=context)
    assert len(signals) == 1
    assert signals[0].metadata["instrument_type"] == "CE"
    assert signals[0].metadata["setup_type"] == "OVERSOLD_REVERSION"


def test_shadow_breadth_institutional_flow_strategy():
    from natras.strategies.breadth_trend_flow import BreadthInstitutionalFlowStrategy

    strat = BreadthInstitutionalFlowStrategy(fast_ema_period=3, slow_ema_period=5, volume_ma_period=5, volume_surge_multiplier=1.2)
    t = datetime(2026, 10, 14, 10, 0, 0, tzinfo=TZ)
    # Feed previous candles (>= 10 candles) where fast EMA is below slow EMA
    prices = [24450.0] * 5 + [24440.0, 24430.0, 24400.0, 24380.0, 24360.0]
    for i, p in enumerate(prices):
        c = validate_candle("NSE:NIFTY50-INDEX", t + timedelta(minutes=5 * i), p, p + 5, p - 5, p, 50000.0)
        strat.history.append(c)

    context = {
        "breadth": {"advance_decline_ratio": 1.65, "bullish_sector_count": 8, "bearish_sector_count": 2},
        "institutional": {"fii_net_flow_cr": 1250.0, "dii_net_flow_cr": 500.0},
    }
    # Explosive upward candle triggering bullish EMA crossover and volume surge
    eval_c = validate_candle("NSE:NIFTY50-INDEX", t + timedelta(minutes=30), 24360.0, 24510.0, 24355.0, 24500.0, 100000.0)
    signals = strat.on_candle(eval_c, context=context)
    assert len(signals) == 1
    assert signals[0].metadata["instrument_type"] == "CE"
    assert signals[0].metadata["setup_type"] == "INSTITUTIONAL_BREADTH_EXPANSION"


