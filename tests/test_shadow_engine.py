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
