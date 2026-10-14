"""Unit tests for the AutoHealer security and self-repair engine."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from natras.resilience.auto_healer import AutoHealer, IncidentReport


def test_record_incident_and_logging(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    notifier = MagicMock()

    healer = AutoHealer(
        telegram_notifier=notifier,
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    inc = healer.record_incident(
        category="NETWORK_DROP",
        action_taken="Paused feed and waited for network recovery",
        details="Wi-Fi was disconnected",
        downtime_seconds=12.5,
    )

    assert inc.category == "NETWORK_DROP"
    assert inc.downtime_seconds == 12.5
    assert log_file.exists()
    assert "NETWORK_DROP" in log_file.read_text(encoding="utf-8")
    assert notifier.called
    assert "NAT RAS SELF-REPAIR" in notifier.call_args[0][0]


def test_guarded_context_manager_containment(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    healer = AutoHealer(
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    with healer.guard(context="test_math", fallback=42) as state:
        val = 1 / 0

    assert not state["success"]
    assert isinstance(state["error"], ZeroDivisionError)
    assert state["fallback"] == 42
    assert len(healer.incidents) == 1
    assert healer.incidents[0].category == "EXCEPTION_CONTAINED"


def test_guarded_decorator(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    healer = AutoHealer(
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    @healer.guarded(context="faulty_function", fallback="SAFE_FALLBACK")
    def faulty():
        raise KeyError("missing_key")

    res = faulty()
    assert res == "SAFE_FALLBACK"


def test_heartbeat_monitoring(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    healer = AutoHealer(
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    healer.write_heartbeat({"open_positions": 2})
    assert hb_file.exists()

    healthy, age = healer.is_heartbeat_healthy(max_age_seconds=60.0)
    assert healthy
    assert age >= 0.0

    # Test stale
    healthy_stale, _ = healer.is_heartbeat_healthy(max_age_seconds=0.0)
    assert not healthy_stale


def test_clear_stale_lock(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    healer = AutoHealer(
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    lock_file = tmp_path / "test.lock"
    # Write definitely dead PID
    lock_file.write_text("99999999")

    cleared = healer.clear_stale_lock(lock_file)
    assert cleared
    assert not lock_file.exists()


def test_telegram_rate_limiting(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    notifier = MagicMock()
    healer = AutoHealer(
        telegram_notifier=notifier,
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )

    # First alert should trigger Telegram
    healer.record_incident("NETWORK_DROP", "Action 1", "Drop 1")
    assert notifier.call_count == 1

    # Immediate second alert in same category should be suppressed by rate-limiter
    healer.record_incident("NETWORK_DROP", "Action 2", "Drop 2")
    assert notifier.call_count == 1

    # Different category should notify
    healer.record_incident("FEED_STALL", "Action 3", "Stall 1")
    assert notifier.call_count == 2


def test_status_summary_formatting(tmp_path):
    log_file = tmp_path / "healer.log"
    hb_file = tmp_path / "heartbeat.json"
    inc_file = tmp_path / "incidents.json"
    healer = AutoHealer(
        log_path=log_file,
        heartbeat_path=hb_file,
        incident_store_path=inc_file,
    )
    healer.write_heartbeat()

    summary = healer.get_status_summary()
    assert "SECURITY & AUTO-HEALER STATUS" in summary
    assert "Watchdog State" in summary
