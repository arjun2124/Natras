"""Unit tests for Upstox historical spot downloader."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from natras.cli.upstox_download import (
    CANDLES_PER_DAY_5M,
    DB_SYMBOL,
    DownloadError,
    days_with_missing,
    fetch_chunk,
    main,
    save,
    to_ist_iso,
)
from natras.data.session import IST


def test_to_ist_iso():
    utc_str = "2026-10-14T03:45:00Z"
    ist_iso = to_ist_iso(utc_str)
    assert "+05:30" in ist_iso
    dt = datetime.fromisoformat(ist_iso)
    assert dt.hour == 9
    assert dt.minute == 15


def test_fetch_chunk_success():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-10-14T09:15:00+05:30", 25000.0, 25050.0, 24980.0, 25020.0, 1000, 0]
            ]
        },
    }
    session.get.return_value = resp

    candles = fetch_chunk(session, "NSE_INDEX|Nifty 50", 5, "2026-10-01", "2026-10-14")
    assert len(candles) == 1
    assert candles[0][1] == 25000.0


def test_fetch_chunk_rate_limit_retry_then_success():
    session = MagicMock()
    resp_429 = MagicMock()
    resp_429.status_code = 429

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"data": {"candles": [["2026-10-14T09:15:00+05:30", 1, 2, 3, 4, 5]]}}

    session.get.side_effect = [resp_429, resp_200]

    with patch("time.sleep"):
        candles = fetch_chunk(session, "NSE_INDEX|Nifty 50", 5, "2026-10-01", "2026-10-14")
    assert len(candles) == 1


def test_fetch_chunk_http_error():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Internal Server Error"
    session.get.return_value = resp

    with pytest.raises(DownloadError) as exc_info:
        fetch_chunk(session, "NSE_INDEX|Nifty 50", 5, "2026-10-01", "2026-10-14")
    assert "HTTP 500" in str(exc_info.value)


def test_save_and_deduplicate(tmp_path):
    db_file = tmp_path / "test_market.db"
    db = sqlite3.connect(db_file)
    db.execute(
        """CREATE TABLE candles(
        symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))"""
    )

    rows = [
        ["2026-10-14T09:15:00+05:30", 25000.0, 25050.0, 24980.0, 25020.0, 100],
        ["2026-10-14T09:20:00+05:30", 25020.0, 25040.0, 25000.0, 25010.0, 200],
        # Malformed / short row: should be skipped
        ["2026-10-14T09:25:00+05:30"],
        # None in OHLC: should be skipped
        ["2026-10-14T09:30:00+05:30", None, 25040.0, 25000.0, 25010.0, 200],
    ]

    retrieved_at = datetime.now(IST).isoformat()
    inserted = save(db, rows, retrieved_at)
    assert inserted == 2

    # Duplicate insertion
    inserted_again = save(db, rows, retrieved_at)
    assert inserted_again == 0

    count = db.execute("SELECT count(*) FROM candles").fetchone()[0]
    assert count == 2
    db.close()


def test_days_with_missing(tmp_path):
    db_file = tmp_path / "test_market.db"
    db = sqlite3.connect(db_file)
    db.execute(
        """CREATE TABLE candles(
        symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))"""
    )

    # Insert 75 candles for 2026-10-13 (full day)
    for i in range(75):
        ts = f"2026-10-13T{9 + i // 60:02d}:{i % 60:02d}:00+05:30"
        db.execute(
            "INSERT INTO candles VALUES (?, ?, 25000, 25050, 24950, 25000, 100, 'test', 'now')",
            (DB_SYMBOL, ts),
        )

    # Insert 10 candles for 2026-10-14 (partial day)
    for i in range(10):
        ts = f"2026-10-14T09:{15 + i * 5:02d}:00+05:30"
        db.execute(
            "INSERT INTO candles VALUES (?, ?, 25000, 25050, 24950, 25000, 100, 'test', 'now')",
            (DB_SYMBOL, ts),
        )
    db.commit()

    missing = days_with_missing(db, 5, date(2026, 10, 13), date(2026, 10, 14))
    assert missing == ["2026-10-14"]

    # If interval is not 5, returns None
    assert days_with_missing(db, 1, date(2026, 10, 13), date(2026, 10, 14)) is None
    db.close()


def test_main_cli_missing_token(monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "")
    with patch("sys.argv", ["upstox_download"]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert "Set UPSTOX_ACCESS_TOKEN in .env first." in str(exc.value)


def test_main_cli_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "valid_mock_token_12345")
    db_file = tmp_path / "data" / "market.db"
    manifest_dir = tmp_path / "runtime_data" / "datasets" / "manifests"

    mock_candles = [
        [f"2026-10-14T{9 + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00+05:30", 25000.0, 25050.0, 24950.0, 25010.0, 100]
        for i in range(75)
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"candles": mock_candles}}

    with patch("natras.cli.upstox_download.DB_PATH", db_file), \
         patch("natras.cli.upstox_download.MANIFEST_DIR", manifest_dir), \
         patch("natras.cli.upstox_download.CHUNK_DAYS", 30), \
         patch("requests.Session.get", return_value=mock_resp), \
         patch("time.sleep"):

        with patch("sys.argv", ["upstox_download", "--days", "5", "--interval", "5"]):
            code = main()
            assert code == 0

    assert db_file.exists()
    manifest_files = list(manifest_dir.glob("*.json"))
    assert len(manifest_files) == 1

    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert manifest["provider"] == "upstox"
    assert manifest["interval_minutes"] == 5
    assert manifest["data_quality_status"] in ("OK", "PARTIAL")
    assert manifest["new_records_saved"] == 75
