"""Upstox historical option candle downloader (data only, no orders).

Run:
    python -m natras.cli.upstox_option_download --instrument "NSE_FO|41743" --days 30 --interval 5
    python -m natras.cli.upstox_option_download --auto-atm --spot 25000 --days 7 --interval 5

Reads UPSTOX_ACCESS_TOKEN from .env. The token is never written to disk or logged.
Candles are saved into data/market.db (table 'candles', source='upstox_option_history').
Duplicates are ignored. A dataset manifest is written to runtime_data/datasets/manifests/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from natras.cli._env import load_env
from natras.data.session import IST

logger = logging.getLogger(__name__)

BASE = "https://api.upstox.com"
DB_PATH = Path("data") / "market.db"
MANIFEST_DIR = Path("runtime_data") / "datasets" / "manifests"
CHUNK_DAYS = 28
MAX_RETRIES = 4

SCHEMA = """CREATE TABLE IF NOT EXISTS candles(
 symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
 source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))"""


class OptionDownloadError(RuntimeError):
    pass


def fetch_option_chunk(
    session: requests.Session,
    instrument_key: str,
    interval: int,
    start: date,
    end: date,
) -> List[List[Any]]:
    """Fetch candles for an option contract from Upstox v3 API."""
    url = f"{BASE}/v3/historical-candle/{instrument_key}/minutes/{interval}/{end}/{start}"
    for attempt in range(1, MAX_RETRIES + 1):
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("candles", [])
        if resp.status_code == 429:
            time.sleep(2 * attempt)
            continue
        raise OptionDownloadError(
            f"HTTP {resp.status_code} for {instrument_key} {start}..{end}: {resp.text[:200]}"
        )
    raise OptionDownloadError(f"Rate limited for {instrument_key} after {MAX_RETRIES} attempts")


def to_ist_iso(raw_ts: str) -> str:
    return datetime.fromisoformat(raw_ts).astimezone(IST).isoformat()


def save_option_candles(
    db: sqlite3.Connection,
    symbol: str,
    rows: List[List[Any]],
    retrieved_at: str,
) -> int:
    before = db.total_changes
    cols = {r[1] for r in db.execute("PRAGMA table_info(candles)").fetchall()}
    has_oi = "open_interest" in cols
    for c in rows:
        if len(c) < 6 or None in c[1:5]:
            continue
        vol = float(c[5]) if len(c) > 5 and c[5] is not None else 0.0
        if has_oi:
            oi = float(c[6]) if len(c) > 6 and c[6] is not None else 0.0
            db.execute(
                "INSERT OR IGNORE INTO candles (symbol, ts, open, high, low, close, volume, source, retrieved_at, open_interest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (symbol, to_ist_iso(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), vol,
                 "upstox_option_history", retrieved_at, oi),
            )
        else:
            db.execute(
                "INSERT OR IGNORE INTO candles (symbol, ts, open, high, low, close, volume, source, retrieved_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (symbol, to_ist_iso(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), vol,
                 "upstox_option_history", retrieved_at),
            )
    db.commit()
    return db.total_changes - before


def download_option_history(
    instrument_key: str,
    days: int = 30,
    interval: int = 5,
    symbol_name: Optional[str] = None,
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    option_type: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Download historical option candles and produce manifest record."""
    tok = token or os.environ.get("UPSTOX_ACCESS_TOKEN", "")
    if not tok or "your_" in tok:
        raise OptionDownloadError("Set UPSTOX_ACCESS_TOKEN in .env first.")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/json"})

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(SCHEMA)

    db_symbol = symbol_name or instrument_key
    end = date.today()
    overall_start = end - timedelta(days=days)
    retrieved_at = datetime.now(IST).isoformat()
    seen = Counter()
    inserted = 0
    errors: List[str] = []

    cursor_end = end
    while cursor_end > overall_start:
        cursor_start = max(overall_start, cursor_end - timedelta(days=CHUNK_DAYS))
        try:
            rows = fetch_option_chunk(session, instrument_key, interval, cursor_start, cursor_end)
            for c in rows:
                seen[to_ist_iso(c[0])] += 1
            inserted += save_option_candles(db, db_symbol, rows, retrieved_at)
        except Exception as exc:
            errors.append(f"{cursor_start}..{cursor_end}: {type(exc).__name__}: {exc}")
        cursor_end = cursor_start - timedelta(days=1)
        time.sleep(0.3)

    duplicates = sum(n - 1 for n in seen.values() if n > 1)
    db.close()

    status = "FAILED" if errors and not seen else ("PARTIAL" if errors else "OK")
    clean_key = instrument_key.replace("|", "_").replace(":", "_")
    dataset_id = f"upstox-opt-{clean_key}-{interval}m-{overall_start}-{end}"
    manifest = {
        "dataset_id": dataset_id,
        "provider": "upstox",
        "endpoint": "/v3/historical-candle",
        "instrument": instrument_key,
        "symbol": db_symbol,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "interval_minutes": interval,
        "start_date": str(overall_start),
        "end_date": str(end),
        "download_timestamp": retrieved_at,
        "records_received": sum(seen.values()),
        "new_records_saved": inserted,
        "duplicate_count": duplicates,
        "errors": errors,
        "field_labels": {
            "ohlcv": "BROKER_PROVIDED",
            "open_interest": "BROKER_PROVIDED" if seen else "UNAVAILABLE",
            "greeks": "UNAVAILABLE",
            "iv": "UNAVAILABLE",
        },
        "data_quality_status": status,
    }

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / f"{dataset_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Upstox Historical Option Candles Downloader")
    parser.add_argument("--instrument", type=str, required=True, help="Upstox instrument key (e.g. NSE_FO|41743)")
    parser.add_argument("--symbol-name", type=str, default=None, help="Local DB symbol name (e.g. NSE:NIFTY26O1317350CE)")
    parser.add_argument("--days", type=int, default=30, help="Days of history to download")
    parser.add_argument("--interval", type=int, default=5, help="Candle interval in minutes")
    parser.add_argument("--expiry", type=str, default=None, help="Option expiry date YYYY-MM-DD")
    parser.add_argument("--strike", type=float, default=None, help="Option strike price")
    parser.add_argument("--type", type=str, choices=["CE", "PE"], default=None, help="Option type")

    args = parser.parse_args()
    try:
        manifest = download_option_history(
            instrument_key=args.instrument,
            days=args.days,
            interval=args.interval,
            symbol_name=args.symbol_name,
            expiry=args.expiry,
            strike=args.strike,
            option_type=args.type,
        )
        print(f"Downloaded {manifest['new_records_saved']} option candles. Status: {manifest['data_quality_status']}")
        print(f"Manifest saved to: {MANIFEST_DIR / (manifest['dataset_id'] + '.json')}")
        return 0 if manifest["data_quality_status"] != "FAILED" else 1
    except Exception as exc:
        print(f"Option download failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
