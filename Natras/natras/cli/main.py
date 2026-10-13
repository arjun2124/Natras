"""NAT RAS command-line diagnostic and inspection tooling."""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from natras.adapters.fyers import FyersAdapter
from natras.adapters.telegram import TelegramNotifier
from natras.adapters.upstox import UpstoxAdapter
from natras.config.constants import MARKET_TIMEZONE_STR, STRATEGY_FAMILY
from natras.config.settings import load_settings
from natras.db.database import DatabaseManager
from natras.research.scaffolding import ResearchScaffolding
from natras.safety.guards import SafetyGuard
from natras.shadow.runner import ShadowRunner
from natras.strategies.baseline_placeholder import BaselineMomentumWickStrategy
from natras.strategies.registry import StrategyRegistry


def print_status() -> None:
    """Print comprehensive system status and module readiness."""
    settings = load_settings()
    db = DatabaseManager(settings.database_path)
    registry = StrategyRegistry()
    guard = SafetyGuard(settings)

    now_ist = datetime.now(ZoneInfo(MARKET_TIMEZONE_STR))
    entry_allowed, entry_reason = guard.is_entry_allowed(now_ist)

    print("================================================================")
    print("                     NAT RAS SYSTEM STATUS                      ")
    print("================================================================")
    print(f"Mode:                  PAPER ONLY (Fail-Closed)")
    print(f"Live Orders Allowed:   {settings.live_orders_enabled} (MANDATORY FALSE)")
    print(f"Current Time (IST):    {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"15:00 IST Entry Cutoff:{'PERMITTED' if entry_allowed else 'BLOCKED'}")
    print(f"Cutoff Detail:         {entry_reason}")
    print(f"Strategy Family Lock:  {STRATEGY_FAMILY}")
    print(f"Database Path:         {settings.database_path.resolve()}")
    print("----------------------------------------------------------------")
    print("STRATEGY REGISTRY:")
    for s in registry.list_strategies():
        print(f"  - [{s['status']}] {s['strategy_id']} ({s['family']})")
        print(f"    Message: {s.get('message', '')}")
    print("----------------------------------------------------------------")
    print("MODULE READINESS:")
    print("  [IMPLEMENTED]     Safety Guards (Fail-Closed, 15:00 IST Cutoff)")
    print("  [IMPLEMENTED]     Configuration Loader & Secret Redaction")
    print("  [IMPLEMENTED]     SQLite Persistence, Schema & Trade Journal")
    print("  [IMPLEMENTED]     Risk Governor (Loss & Sizing Limits)")
    print("  [IMPLEMENTED]     Baseline Strategy Logic (v2.2.0-RESEARCH-MOMENTUM-WICK)")
    print("  [PARTIAL]         Paper Executor (Offline Component - No Live Loop / Slippage)")
    print("  [PARTIAL]         5-Minute Candle Engine (Validation Core - No Live Stream)")
    print(f"  [{FyersAdapter.status.value}] FYERS Live Data / Broker Adapter")
    print(f"  [{UpstoxAdapter.status.value}] Upstox Historical / Audit Adapter")
    print(f"  [{TelegramNotifier.status.value}] Telegram Notification Client")
    print(f"  [{ResearchScaffolding.status.value}] Research Scaffolding")
    print(f"  [{ShadowRunner.status.value}] Shadow Candidate Runner")
    print("================================================================")


def run_verify() -> int:
    """Run self-diagnostic verification of all safety controls."""
    print("Running NAT RAS Safety & Baseline Diagnostics...")
    settings = load_settings()

    # 1. Live orders disabled check
    assert not settings.live_orders_enabled, "FATAL: live_orders_enabled is not False!"
    print("  [PASS] Fail-closed: live_orders_enabled is False.")

    # 2. Safety guard check
    guard = SafetyGuard(settings)
    guard.verify_paper_only_mode()
    print("  [PASS] Paper-only guard verification.")

    # 3. 15:00 IST cutoff check
    tz = ZoneInfo(MARKET_TIMEZONE_STR)
    before_cutoff = datetime(2026, 10, 14, 14, 40, 0, tzinfo=tz)
    after_cutoff = datetime(2026, 10, 14, 15, 0, 0, tzinfo=tz)
    allowed, _ = guard.is_entry_allowed(before_cutoff)
    assert allowed is True, "Cutoff failed to permit 14:40 entry."
    blocked, _ = guard.is_entry_allowed(after_cutoff)
    assert blocked is False, "Cutoff failed to block 15:00 entry."
    print("  [PASS] 15:00 IST entry cutoff enforcement.")

    # 4. Confirmed baseline strategy check
    from natras.data.candle_engine import validate_candle
    from natras.strategies.momentum_wick import MomentumWickStrategy

    strat = MomentumWickStrategy()
    assert strat.status.value == "ACTIVE_PAPER"
    c_valid = validate_candle(
        "NIFTY",
        before_cutoff.replace(hour=10, minute=0),
        24500.0,
        24535.0,
        24495.0,
        24530.0,
        10000.0,
        atr14=15.0,
    )
    sig = strat.on_candle(c_valid)
    assert len(sig) == 1, "Strategy failed to generate signal on valid momentum candle"
    assert sig[0].metadata["instrument_type"] == "CE"
    print("  [PASS] Confirmed baseline strategy generates valid signals.")

    print("\nALL SYSTEM SAFETY DIAGNOSTICS PASSED.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="NAT RAS Platform CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("status", help="Display platform readiness and configuration")
    subparsers.add_parser("verify", help="Run safety and compliance diagnostics")

    args = parser.parse_args()

    if args.command == "verify":
        code = run_verify()
        sys.exit(code)
    else:
        # Default to status
        print_status()


if __name__ == "__main__":
    main()
