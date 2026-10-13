'''Upstox historical downloader (data only, no orders).

Run: python -m natras.cli.upstox_download [--days 365] [--interval 5]

Reads UPSTOX_ACCESS_TOKEN from the environment / .env. The token is never written
to disk, logged or printed. Candles are saved into data/market.db using the same
table as the live collector (source='upstox_history'); duplicates are ignored.
A dataset manifest is written to runtime_data/datasets/manifests/.

Check endpoint behaviour against current Upstox docs; a failure is reported, never faked.
'''
import argparse
import json
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from natras.cli._env import load_env
from natras.data.session import IST

BASE = 'https://api.upstox.com'
NIFTY_KEY = 'NSE_INDEX|Nifty 50'
DB_SYMBOL = 'NSE:NIFTY50-INDEX'
DB_PATH = Path('data') / 'market.db'
MANIFEST_DIR = Path('runtime_data') / 'datasets' / 'manifests'
CHUNK_DAYS = 28
CANDLES_PER_DAY_5M = 75
MAX_RETRIES = 4

SCHEMA = '''CREATE TABLE IF NOT EXISTS candles(
 symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
 source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))'''


class DownloadError(RuntimeError):
    pass


def fetch_chunk(session, key, interval, start, end):
    url = f'{BASE}/v3/historical-candle/{key}/minutes/{interval}/{end}/{start}'
    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.json().get('data', {}).get('candles', [])
        if resp.status_code == 429:
            time.sleep(2 * attempt)
            continue
        raise DownloadError(f'HTTP {resp.status_code} for {start}..{end}: {resp.text[:200]}')
    raise DownloadError(f'Rate limited for {start}..{end} after {MAX_RETRIES} attempts')


def to_ist_iso(raw_ts):
    return datetime.fromisoformat(raw_ts).astimezone(IST).isoformat()


def save(db, rows, retrieved_at):
    before = db.total_changes
    for c in rows:
        if len(c) < 6 or None in c[1:5]:
            continue
        db.execute(
            'INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?,?)',
            (DB_SYMBOL, to_ist_iso(c[0]), c[1], c[2], c[3], c[4], c[5] or 0,
             'upstox_history', retrieved_at),
        )
    db.commit()
    return db.total_changes - before


def days_with_missing(db, interval, start, end):
    if interval != 5:
        return None
    rows = db.execute(
        'SELECT substr(ts,1,10), COUNT(*) FROM candles WHERE symbol=? AND substr(ts,1,10) BETWEEN ? AND ? '
        'GROUP BY substr(ts,1,10)', (DB_SYMBOL, str(start), str(end)),
    ).fetchall()
    return sorted(d for d, n in rows if n != CANDLES_PER_DAY_5M)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()

    token = os.environ.get('UPSTOX_ACCESS_TOKEN', '')
    if not token or 'your_' in token:
        raise SystemExit('Set UPSTOX_ACCESS_TOKEN in .env first.')

    session = requests.Session()
    session.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/json'})

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(SCHEMA)

    end = date.today()
    overall_start = end - timedelta(days=args.days)
    retrieved_at = datetime.now(IST).isoformat()
    seen = Counter()
    inserted = 0
    errors = []
    cursor_end = end
    while cursor_end > overall_start:
        cursor_start = max(overall_start, cursor_end - timedelta(days=CHUNK_DAYS))
        try:
            rows = fetch_chunk(session, NIFTY_KEY, args.interval, cursor_start, cursor_end)
            for c in rows:
                seen[to_ist_iso(c[0])] += 1
            inserted += save(db, rows, retrieved_at)
            print(f'{cursor_start}..{cursor_end}: {len(rows)} candles', flush=True)
        except (DownloadError, requests.RequestException) as exc:
            errors.append(f'{cursor_start}..{cursor_end}: {type(exc).__name__}: {exc}')
            print(f'FAILED {cursor_start}..{cursor_end}: {exc}', flush=True)
        cursor_end = cursor_start - timedelta(days=1)
        time.sleep(0.4)

    duplicates = sum(n - 1 for n in seen.values() if n > 1)
    missing_days = days_with_missing(db, args.interval, overall_start, end)
    db.close()

    status = 'FAILED' if errors and not seen else ('PARTIAL' if errors or missing_days else 'OK')
    manifest = {
        'dataset_id': f'upstox-nifty50-{args.interval}m-{overall_start}-{end}',
        'provider': 'upstox',
        'endpoint': '/v3/historical-candle',
        'instrument': NIFTY_KEY,
        'interval_minutes': args.interval,
        'start_date': str(overall_start),
        'end_date': str(end),
        'download_timestamp': retrieved_at,
        'records_received': sum(seen.values()),
        'new_records_saved': inserted,
        'duplicate_count': duplicates,
        'days_with_unexpected_candle_count': missing_days,
        'errors': errors,
        'field_labels': {'ohlcv': 'BROKER_PROVIDED', 'greeks': 'UNAVAILABLE', 'iv': 'UNAVAILABLE'},
        'data_quality_status': status,
    }
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"{manifest['dataset_id']}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Saved {inserted} new candles. Status: {status}. Manifest: {path}', flush=True)
    return 0 if status != 'FAILED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
