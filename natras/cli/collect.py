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

DB_PATH = Path('data') / 'market.db'
DEFAULT_SYMBOLS = ['NSE:NIFTY50-INDEX']
STOP_HOUR, STOP_MIN = 15, 30

SCHEMA = '''CREATE TABLE IF NOT EXISTS candles(
 symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
 source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))'''


def _save(db, candle, source):
    db.execute(
        'INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?,?)',
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
        orch = MarketIntelligenceOrchestrator(db_manager=natras_db)
        print('Settling initial market intelligence & news snapshot...', flush=True)
        orch.poll_and_settle(natras_db)

        telegram_bot = TelegramLiveBot(db_manager=natras_db)
        paper_runtime = PaperRuntime(
            db=natras_db,
            telegram_bot=telegram_bot,
            quote_fetcher=adapter.fetch_quotes,
        )
        paper_runtime.load_option_chain_from_master()
        print('Paper trading runtime and Telegram command listener active.', flush=True)
    except Exception as exc:
        print(f'Warning: Runtime extensions skipped: {exc}', flush=True)
        orch = None
        natras_db = None
        telegram_bot = None
        paper_runtime = None

    def on_candle(c):
        with lock:
            _save(db, c, 'fyers_live')
        print(f'{c.symbol} {c.timestamp:%H:%M} C={c.close}', flush=True)
        if paper_runtime and 'NIFTY50' in c.symbol:
            try:
                results = paper_runtime.on_spot_candle(c)
                for res in results:
                    print(f'Paper Order: {res.order_id} fill={res.fill_price}', flush=True)
            except Exception as exc:
                print(f'Paper execution error: {exc}', flush=True)

    feed = FyersFeed(creds.app_id, token, symbols, on_candle)
    threading.Thread(target=feed.start, daemon=True).start()

    last_health = 0.0
    last_intel = time.time()
    try:
        while True:
            now = datetime.now(IST)
            if (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
                break
            feed.close_completed(now)
            if time.time() - last_health > 60:
                last_health = time.time()
                st = feed.get_status()
                if not st['healthy']:
                    print(f"UNHEALTHY: {st['last_error']}", flush=True)

            # Periodically poll and settle market intelligence every 15 minutes
            if orch and (time.time() - last_intel > 900):
                last_intel = time.time()
                try:
                    orch.poll_and_settle(natras_db)
                    print(f'[{now:%H:%M}] Market intelligence & news snapshot settled.', flush=True)
                except Exception as exc:
                    print(f'Market intelligence periodic settlement warning: {exc}', flush=True)

            # Poll incoming Telegram commands from authorized chat
            if telegram_bot:
                try:
                    telegram_bot.poll_once()
                except Exception:
                    pass

            time.sleep(2)
    finally:
        feed.stop()
        if paper_runtime:
            try:
                paper_runtime.square_off_all_positions(reason="COLLECTOR_STOPPED")
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
    print('Collector stopped.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
