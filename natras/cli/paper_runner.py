"""Paper trading engine CLI runner.

Run:
    python -m natras.cli.paper_runner --live
    python -m natras.cli.paper_runner --replay-today
    python -m natras.cli.paper_runner --status

Flow:
1. Validates FYERS token & loads option chain from symbol master.
2. Ingests completed 5-minute NIFTY spot candles.
3. Evaluates momentum wick strategy, selects real option strikes, executes paper orders.
4. Manages dynamic stepped trailing stop, respects 15:00 IST entry cutoff and EOD square-off.
5. Dispatches notifications to Telegram bot.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from natras.adapters.fyers_auth import FyersAuthenticator, FyersCredentials
from natras.adapters.fyers_live import FyersFeed, FyersLiveAdapter
from natras.adapters.telegram_live import TelegramLiveBot
from natras.cli._env import load_env
from natras.config.constants import MARKET_TIMEZONE_STR
from natras.data.candle_engine import validate_candle
from natras.data.session import IST
from natras.db.database import DatabaseManager
from natras.execution.paper_runtime import PaperRuntime

logger = logging.getLogger("natras.paper_runner")

DB_PATH = Path("data") / "market.db"
LOG_PATH = Path("data") / "logs" / "paper_runner.log"


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="NAT RAS Paper Trading Engine")
    parser.add_argument("--live", action="store_true", help="Run live paper trading on incoming FYERS feed")
    parser.add_argument("--replay-today", action="store_true", help="Replay today's candles from data/market.db")
    parser.add_argument("--status", action="store_true", help="Print current paper positions and PnL")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )

    db = DatabaseManager()
    telegram = TelegramLiveBot(db_manager=db)

    # Initialize quote fetcher via FYERS adapter if credentials available
    quote_fetcher = None
    try:
        creds = FyersCredentials.from_env()
        auth = FyersAuthenticator(creds)
        token = auth.get_access_token()
        adapter = FyersLiveAdapter(creds.app_id, token)
        quote_fetcher = adapter.fetch_quotes
    except Exception as exc:
        logger.warning("Live quote adapter not initialized: %s", exc)

    runtime = PaperRuntime(
        db=db,
        telegram_bot=telegram,
        quote_fetcher=quote_fetcher,
    )
    runtime.load_option_chain_from_master()

    if args.status:
        print(telegram.handle_command("/paper_status"))
        print("\n" + telegram.handle_command("/open_positions"))
        return 0

    if args.replay_today:
        if not DB_PATH.exists():
            print("No market database found at data/market.db.")
            return 1
        print("Replaying today's candles from database through paper runtime...")
        conn = sqlite3.connect(DB_PATH)
        today = datetime.now(IST).date().isoformat()
        rows = conn.execute(
            "SELECT symbol, ts, open, high, low, close, volume FROM candles "
            "WHERE symbol = 'NSE:NIFTY50-INDEX' AND ts LIKE ? ORDER BY ts ASC",
            (today + "%",),
        ).fetchall()
        conn.close()

        if not rows:
            print("No candles found for today in data/market.db.")
            return 0

        for r in rows:
            ts = datetime.fromisoformat(r[1])
            candle = validate_candle(r[0], ts, r[2], r[3], r[4], r[5], r[6])
            results = runtime.on_spot_candle(candle)
            for res in results:
                print(f"[{ts.strftime('%H:%M')}] Order Fill: {res.order_id} at {res.fill_price}")

        print("\nReplay finished.")
        print(telegram.handle_command("/paper_status"))
        return 0

    # Live feed execution loop
    if quote_fetcher is None:
        print("Cannot start live paper trading without valid FYERS session. Run python -m natras.cli.fyers_auto_login first.")
        return 1

    telegram.send_message("🚀 *NAT RAS PAPER TRADING RUNTIME STARTED*\n• Mode: Paper only (Fail-Closed)\n• Listening for completed 5m candles.")
    print("Paper trading engine running. Press Ctrl+C to stop.")

    def on_live_candle(candle):
        logger.info("Received candle %s close=%.2f", candle.timestamp.strftime("%H:%M"), candle.close)
        results = runtime.on_spot_candle(candle)
        for res in results:
            logger.info("Paper order executed: %s fill=%.2f", res.order_id, res.fill_price or 0.0)

    feed = FyersFeed(creds.app_id, token, ["NSE:NIFTY50-INDEX"], on_live_candle)
    import threading
    threading.Thread(target=feed.start, daemon=True).start()

    try:
        while True:
            now_ist = datetime.now(ZoneInfo(MARKET_TIMEZONE_STR))
            if (now_ist.hour, now_ist.minute) >= (15, 30):
                logger.info("Market close reached (15:30 IST); stopping paper runner.")
                break
            # Close completed candle bucket if on time
            feed.close_completed(now_ist)
            # Poll Telegram commands
            telegram.poll_once()
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        feed.stop()
        runtime.square_off_all_positions(reason="ENGINE_STOPPED")
        telegram.send_message("🛑 *NAT RAS PAPER TRADING STOPPED*")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
