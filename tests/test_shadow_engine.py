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
    assert "Experimental_Aggressive_Body_0.40" in names
    assert "Experimental_High_Conviction_0.60" in names
    assert "Experimental_Tight_Trail_6_2" in names
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

