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

    def on_candle(c):
        with lock:
            _save(db, c, 'fyers_live')
        print(f'{c.symbol} {c.timestamp:%H:%M} C={c.close}', flush=True)

    feed = FyersFeed(creds.app_id, token, symbols, on_candle)
    threading.Thread(target=feed.start, daemon=True).start()

    last_health = 0.0
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
            time.sleep(2)
    finally:
        feed.stop()
        db.close()
    print('Collector stopped.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
