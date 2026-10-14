'''Data-only collector. Run: python -m natras.cli.collect [SYMBOL ...]

No orders are placed. Saves validated 5-minute candles to data/market.db.
'''
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from natras.adapters.fyers_auth import FyersAuthenticator, FyersCredentials
from natras.adapters.fyers_live import FyersFeed, FyersLiveAdapter
from natras.cli._env import load_env
from natras.data.session import IST
from natras.resilience.auto_healer import AutoHealer

DB_PATH = Path('data') / 'market.db'
DEFAULT_SYMBOLS = ['NSE:NIFTY50-INDEX']
STOP_HOUR, STOP_MIN = 15, 30

SCHEMA = '''CREATE TABLE IF NOT EXISTS candles(
 symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
 source TEXT, retrieved_at TEXT, open_interest REAL DEFAULT 0.0, PRIMARY KEY(symbol, ts))'''


def _save(db, candle, source):
    oi = getattr(candle, 'open_interest', 0.0) or 0.0
    cols = {r[1] for r in db.execute('PRAGMA table_info(candles)').fetchall()}
    if 'open_interest' in cols:
        db.execute(
            'INSERT OR IGNORE INTO candles (symbol, ts, open, high, low, close, volume, source, retrieved_at, open_interest) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            (candle.symbol, candle.timestamp.isoformat(), candle.open, candle.high,
             candle.low, candle.close, candle.volume, source, datetime.now(IST).isoformat(), oi),
        )
    else:
        db.execute(
            'INSERT OR IGNORE INTO candles (symbol, ts, open, high, low, close, volume, source, retrieved_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (candle.symbol, candle.timestamp.isoformat(), candle.open, candle.high,
             candle.low, candle.close, candle.volume, source, datetime.now(IST).isoformat()),
        )
    db.commit()



def main() -> int:
    load_env()
    symbols = sys.argv[1:] or DEFAULT_SYMBOLS
    creds = FyersCredentials.from_env()
    token = FyersAuthenticator(creds).get_access_token()
    adapter = FyersLiveAdapter(creds.app_id, token)
    adapter.authenticate()
    print('FYERS token valid.', flush=True)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute(SCHEMA)
    lock = threading.Lock()

    today = datetime.now(IST).date()
    for s in symbols:
        try:
            rows = adapter.fetch_candles(s, '5', today - timedelta(days=5), today)
            with lock:
                for c in rows:
                    _save(db, c, 'fyers_history')
            print(f'Backfilled {len(rows)} candles for {s}.', flush=True)
        except Exception as exc:
            print(f'Backfill failed for {s}: {type(exc).__name__}: {exc}', flush=True)

    # Initialize Market Intelligence settlement engine and Paper Trading Runtime
    try:
        from natras.db.database import DatabaseManager
        from natras.market_intelligence.orchestrator import MarketIntelligenceOrchestrator
        from natras.adapters.telegram_live import TelegramLiveBot
        from natras.execution.paper_runtime import PaperRuntime

        natras_db = DatabaseManager()
        orch = MarketIntelligenceOrchestrator(
            db_manager=natras_db,
            quote_fetcher=adapter.fetch_quotes,
        )
        print('Settling initial market intelligence & news snapshot...', flush=True)
        orch.poll_and_settle(natras_db)

        telegram_bot = TelegramLiveBot(db_manager=natras_db)
        healer = AutoHealer.get_instance()
        healer.telegram_notifier = telegram_bot.send_message

        paper_runtime = PaperRuntime(
            db=natras_db,
            telegram_bot=telegram_bot,
            quote_fetcher=adapter.fetch_quotes,
        )
        paper_runtime.load_option_chain_from_master()
        print('Paper trading runtime and Telegram command listener active.', flush=True)
        if paper_runtime.shadow_engine:
            print(f'Shadow strategy engine loaded ({len(paper_runtime.shadow_engine.candidates)} variants active).', flush=True)
        print('AutoHealer silent security & self-repair layer active.', flush=True)
    except Exception as exc:
        print(f'Warning: Runtime extensions skipped: {exc}', flush=True)
        orch = None
        natras_db = None
        telegram_bot = None
        paper_runtime = None
        healer = AutoHealer.get_instance()

    def on_candle(c):
        with healer.guard("candle_save"):
            with lock:
                _save(db, c, 'fyers_live')
        print(f'{c.symbol} {c.timestamp:%H:%M} C={c.close}', flush=True)
        if paper_runtime and 'NIFTY50' in c.symbol:
            with healer.guard("paper_execution"):
                results = paper_runtime.on_spot_candle(c)
                for res in results:
                    print(f'Paper Order: {res.order_id} fill={res.fill_price}', flush=True)

    feed = FyersFeed(creds.app_id, token, symbols, on_candle)
    threading.Thread(target=feed.start, daemon=True).start()

    last_health = 0.0
    last_intel = time.time()
    try:
        while True:
            now = datetime.now(IST)
            if (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
                break

            # 1. Update heartbeat for external supervisor watchdog
            open_count = len(paper_runtime.active_trackers) if paper_runtime else 0
            healer.write_heartbeat({
                "open_positions": open_count,
                "symbols": symbols,
                "last_seen_ist": now.strftime("%H:%M:%S"),
            })

            # 2. Silent Network Self-Repair: check if internet connection dropped
            if not healer.check_internet():
                healer.wait_for_network_recovery(max_wait_seconds=300.0)

            # 3. Guarded candle completion
            with healer.guard("close_completed"):
                feed.close_completed(now)

            if time.time() - last_health > 60:
                last_health = time.time()
                st = feed.get_status()
                if not st['healthy']:
                    print(f"UNHEALTHY: {st['last_error']}", flush=True)
                    # Auto-heal socket if network dropped
                    if "WebSocketAddressException" in str(st['last_error']) or "ConnectionClosed" in str(st['last_error']):
                        healer.logger.info("Attempting auto-recovery of feed socket...")
                        feed.healthy = True

            # 4. Periodically poll and settle market intelligence every 15 minutes
            if orch and (time.time() - last_intel > 900):
                last_intel = time.time()
                with healer.guard("market_intel_settle"):
                    orch.poll_and_settle(natras_db)
                    print(f'[{now:%H:%M}] Market intelligence & news snapshot settled.', flush=True)

            # 5. Operate stepped trailing stop on genuine option premiums for open positions
            if paper_runtime:
                with healer.guard("trailing_stop_poll"):
                    paper_runtime.poll_and_update_open_positions(now)

            # 6. Poll incoming Telegram commands from authorized chat
            if telegram_bot:
                with healer.guard("telegram_poll"):
                    telegram_bot.poll_once()

            time.sleep(2)
    finally:
        feed.stop()
        now = datetime.now(IST)
        # Preserve active positions on mid-day restart; only square off at end of day (15:00+)
        if paper_runtime and (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
            try:
                paper_runtime.square_off_all_positions(reason="EOD_SQUARE_OFF")
            except Exception:
                pass
        db.close()
        # Final EOD settlement
        if orch:
            try:
                orch.poll_and_settle(natras_db)
                print('Final EOD market intelligence settled.', flush=True)
            except Exception:
                pass
        # Final EOD report to Telegram
        if telegram_bot and (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
            try:
                eod_rep = telegram_bot.handle_command('/today_report')
                telegram_bot.send_message(f"🏁 *MARKET SESSION CONCLUDED (15:30 IST)*\n\n{eod_rep}")
                print('Final EOD Telegram report dispatched.', flush=True)
            except Exception:
                pass
    print('Collector stopped.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
