"""NAT RAS command-line diagnostic and inspection tooling."""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from natras.adapters.fyers import FyersAdapter
from natras.adapters.telegram import TelegramNotifier
from natras.adapters.upstox import UpstoxAdapter
from natras.cli._env import load_env
from natras.config.constants import MARKET_TIMEZONE_STR, STRATEGY_FAMILY
from natras.config.settings import load_settings
from natras.db.database import DatabaseManager
from natras.research.scaffolding import ResearchScaffolding
from natras.safety.guards import SafetyGuard
from natras.shadow.runner import ShadowRunner
from natras.strategies.registry import StrategyRegistry

load_env()


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
    print("  [IMPLEMENTED]     Paper Executor (Slippage Rs 0.50, Brokerage Rs 20, Persistent Journal)")
    print("  [IMPLEMENTED]     Paper Trading Runtime (Live Feeds, Trailing Stop, Cutoffs)")
    print("  [IMPLEMENTED]     5-Minute Candle Engine & Real-Time Tick Aggregator")
    print("  [IMPLEMENTED]     FYERS Live Data Feed & Auto-OAuth Authentication")
    print("  [IMPLEMENTED]     Upstox Historical Spot & Option Downloader with Manifests")
    print("  [IMPLEMENTED]     Telegram Notification Bot & Interactive Commands")
    print("  [IMPLEMENTED]     Research Backtester & Candle Replay Engine")
    print("  [IMPLEMENTED]     Shadow Variant Runner & Next-Session Promotion Governance")
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


def inspect_data() -> None:
    """Print comprehensive summary of stored market data, candles, and intelligence."""
    import sqlite3
    from pathlib import Path

    market_db = Path("data") / "market.db"
    natras_db = Path("data") / "natras.db"

    print("================================================================")
    print("                     NAT RAS DATA INVENTORY                     ")
    print("================================================================")
    if not market_db.exists():
        print("Market database not found at data/market.db.")
        return

    conn = sqlite3.connect(market_db)
    cursor = conn.cursor()
    total_candles = cursor.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    print(f"Database Path:        {market_db.resolve()}")
    print(f"Total Stored Candles: {total_candles:,}")
    print("----------------------------------------------------------------")
    print("Candle Breakdown by Source & Symbol:")
    breakdown = cursor.execute(
        "SELECT symbol, source, COUNT(*), MIN(ts), MAX(ts) FROM candles GROUP BY symbol, source"
    ).fetchall()
    for sym, src, count, min_ts, max_ts in breakdown:
        print(f"  • {sym:<24} [{src}] : {count:,} candles ({min_ts[:10]} to {max_ts[:10]})")

    print("----------------------------------------------------------------")
    print("Latest 5 NIFTY Spot 5-Minute Candles:")
    latest_spot = cursor.execute(
        "SELECT ts, open, high, low, close, volume FROM candles "
        "WHERE symbol = 'NSE:NIFTY50-INDEX' ORDER BY ts DESC LIMIT 5"
    ).fetchall()
    for ts, o, h, l, c, v in latest_spot:
        print(f"  {ts} | O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} Vol={v:.0f}")

    print("----------------------------------------------------------------")
    print("Latest 5 Expired Option 1-Minute / 5-Minute Candles:")
    latest_opt = cursor.execute(
        "SELECT symbol, ts, open, high, low, close, volume, open_interest FROM candles "
        "WHERE symbol != 'NSE:NIFTY50-INDEX' ORDER BY ts DESC LIMIT 5"
    ).fetchall()
    for sym, ts, o, h, l, c, v, oi in latest_opt:
        oi_str = f"OI={oi:.0f}" if oi is not None else "OI=N/A"
        print(f"  {sym:<20} | {ts} | C={c:.2f} V={v:.0f} {oi_str}")

    conn.close()

    if natras_db.exists():
        n_conn = sqlite3.connect(natras_db)
        n_cur = n_conn.cursor()
        print("----------------------------------------------------------------")
        print("Market Intelligence & Persistence:")
        try:
            intel_count = n_cur.execute("SELECT COUNT(*) FROM market_intelligence").fetchone()[0]
            news_count = n_cur.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
            print(f"  • Market Intelligence Snapshots : {intel_count:,}")
            print(f"  • News & Macro Items Stored    : {news_count:,}")
        except Exception:
            pass
        n_conn.close()

    print("================================================================")
    print("Tip: To export spot candles to Excel CSV, run:")
    print("     python -m natras.cli.main export-csv")
    print("================================================================")


def export_csv(symbol: str = "NSE:NIFTY50-INDEX", output_path: str = "data/nifty50_candles.csv") -> None:
    """Export candles from SQLite to a CSV file for opening in Excel."""
    import csv
    import sqlite3
    from pathlib import Path

    market_db = Path("data") / "market.db"
    if not market_db.exists():
        print("Market database not found at data/market.db.")
        return

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(market_db)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT symbol, ts, open, high, low, close, volume, source FROM candles "
        "WHERE symbol = ? ORDER BY ts ASC",
        (symbol,),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No candles found for symbol {symbol}.")
        return

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Symbol", "Timestamp", "Open", "High", "Low", "Close", "Volume", "Source"])
        writer.writerows(rows)

    print(f"Exported {len(rows):,} candles for {symbol} to {out_file.resolve()}")
    print("You can now double-click this CSV file to view it in Microsoft Excel or Notepad.")


def main() -> None:
    parser = argparse.ArgumentParser(description="NAT RAS Platform CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("status", help="Display platform readiness and configuration")
    subparsers.add_parser("verify", help="Run safety and compliance diagnostics")
    subparsers.add_parser("catalog", help="Display Upstox data inventory and downloadable datasets")
    subparsers.add_parser("inspect-data", help="Display summary and recent rows of stored market data")
    subparsers.add_parser("export-csv", help="Export candles to CSV for opening in Excel")

    args = parser.parse_args()

    if args.command == "verify":
        code = run_verify()
        sys.exit(code)
    elif args.command == "catalog":
        import os
        from natras.cli.upstox_catalog import decode_jwt_metadata, print_catalog_report, run_catalog_verification
        from natras.adapters.upstox_market_data import UpstoxMarketDataClient
        token = os.environ.get("UPSTOX_ACCESS_TOKEN") or os.environ.get("UPSTOX_ANALYTICS_TOKEN") or ""
        meta = decode_jwt_metadata(token)
        client = UpstoxMarketDataClient(token=token)
        catalog = run_catalog_verification(client)
        print_catalog_report(meta, catalog)
    elif args.command == "inspect-data":
        inspect_data()
    elif args.command == "export-csv":
        export_csv()
    else:
        # Default to status
        print_status()


if __name__ == "__main__":
    main()
