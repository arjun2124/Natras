"""Unit tests for safety guards and 15:00 IST entry cutoff enforcement."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from natras.config.constants import MARKET_TIMEZONE_STR, OrderIntent
from natras.config.settings import LiveTradingProhibitedError, Settings
from natras.safety.guards import EntryCutoffViolationError, SafetyGuard


def test_paper_only_guard_passes_when_false():
    settings = Settings(live_orders_enabled=False)
    guard = SafetyGuard(settings)
    # Should not raise
    guard.verify_paper_only_mode()


def test_safety_guard_rejects_live_orders_if_configured():
    from types import SimpleNamespace
    guard = SafetyGuard()
    guard.settings = SimpleNamespace(live_orders_enabled=True, timezone_name=MARKET_TIMEZONE_STR)
    with pytest.raises(LiveTradingProhibitedError, match="FAIL-CLOSED"):
        guard.verify_paper_only_mode()


def test_entry_cutoff_before_1500_ist(safety_guard: SafetyGuard):
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    # 09:30 AM IST - market open period
    dt = datetime(2026, 10, 14, 9, 30, 0, tzinfo=tz)
    allowed, msg = safety_guard.is_entry_allowed(dt)
    assert allowed is True
    assert "Entry permitted" in msg

    # 14:59:59 IST - right before cutoff
    dt_last_minute = datetime(2026, 10, 14, 14, 59, 59, tzinfo=tz)
    allowed, msg = safety_guard.is_entry_allowed(dt_last_minute)
    assert allowed is True


def test_entry_cutoff_at_and_after_1500_ist(safety_guard: SafetyGuard):
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    # Exact 15:00:00 IST cutoff
    dt_cutoff = datetime(2026, 10, 14, 15, 0, 0, tzinfo=tz)
    allowed, msg = safety_guard.is_entry_allowed(dt_cutoff)
    assert allowed is False
    assert "at or after 15:00 IST cutoff" in msg

    # After cutoff: 15:05:00 IST
    dt_after = datetime(2026, 10, 14, 15, 5, 0, tzinfo=tz)
    allowed, msg = safety_guard.is_entry_allowed(dt_after)
    assert allowed is False

    # Evening/Night check
    dt_night = datetime(2026, 10, 14, 21, 0, 0, tzinfo=tz)
    allowed, _ = safety_guard.is_entry_allowed(dt_night)
    assert allowed is False


def test_utc_conversion_to_ist_for_cutoff(safety_guard: SafetyGuard):
    # 15:00 IST corresponds to 09:30 UTC
    dt_utc_cutoff = datetime(2026, 10, 14, 9, 30, 0, tzinfo=timezone.utc)
    allowed, _ = safety_guard.is_entry_allowed(dt_utc_cutoff)
    assert allowed is False

    # 14:59 IST corresponds to 09:29 UTC
    dt_utc_before = datetime(2026, 10, 14, 9, 29, 0, tzinfo=timezone.utc)
    allowed, _ = safety_guard.is_entry_allowed(dt_utc_before)
    assert allowed is True


def test_validate_order_execution_blocks_entry_after_cutoff(safety_guard: SafetyGuard):
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    dt_after = datetime(2026, 10, 14, 15, 10, 0, tzinfo=tz)

    with pytest.raises(EntryCutoffViolationError, match="Entry prohibited"):
        safety_guard.validate_order_execution(order_intent=OrderIntent.ENTRY, dt=dt_after)


def test_validate_order_execution_allows_exit_after_cutoff(safety_guard: SafetyGuard):
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    dt_after = datetime(2026, 10, 14, 15, 10, 0, tzinfo=tz)

    # Position exit and management MUST be allowed after 15:00 IST
    safety_guard.validate_order_execution(order_intent=OrderIntent.EXIT, dt=dt_after)
