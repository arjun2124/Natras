"""Unit tests for Upstox option candle downloader."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from natras.cli.upstox_option_download import (
    OptionDownloadError,
    download_option_history,
    fetch_option_chunk,
    save_option_candles,
)


def test_save_option_candles(tmp_path):
    db_file = tmp_path / "test_market.db"
    db = sqlite3.connect(db_file)
    db.execute(
        """CREATE TABLE candles(
        symbol TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        source TEXT, retrieved_at TEXT, PRIMARY KEY(symbol, ts))"""
    )

    rows = [
        ["2026-10-14T09:15:00+05:30", 120.0, 130.0, 115.0, 125.0, 500],
        ["2026-10-14T09:20:00+05:30", 125.0, 140.0, 122.0, 135.0, 800],
        ["2026-10-14T09:25:00+05:30"],  # Malformed
    ]
    inserted = save_option_candles(db, "NSE_FO|12345", rows, "2026-10-14T10:00:00+05:30")
    assert inserted == 2

    # Duplicates are skipped
    inserted_dup = save_option_candles(db, "NSE_FO|12345", rows, "2026-10-14T10:00:00+05:30")
    assert inserted_dup == 0
    db.close()


def test_download_option_history_flow(tmp_path):
    db_file = tmp_path / "data" / "market.db"
    manifest_dir = tmp_path / "runtime_data" / "datasets" / "manifests"

    mock_candles = [
        ["2026-10-14T09:15:00+05:30", 100.0, 110.0, 95.0, 105.0, 1000],
        ["2026-10-14T09:20:00+05:30", 105.0, 115.0, 102.0, 112.0, 1200],
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"candles": mock_candles}}

    with patch("natras.cli.upstox_option_download.DB_PATH", db_file), \
         patch("natras.cli.upstox_option_download.MANIFEST_DIR", manifest_dir), \
         patch("requests.Session.get", return_value=mock_resp), \
         patch("time.sleep"):

        manifest = download_option_history(
            instrument_key="NSE_FO|41743",
            days=2,
            interval=5,
            symbol_name="NSE:NIFTY26O1317350CE",
            expiry="2026-10-15",
            strike=17350.0,
            option_type="CE",
            token="test_mock_token",
        )

        assert manifest["provider"] == "upstox"
        assert manifest["instrument"] == "NSE_FO|41743"
        assert manifest["symbol"] == "NSE:NIFTY26O1317350CE"
        assert manifest["new_records_saved"] == 2
        assert manifest["data_quality_status"] == "OK"
        assert manifest_dir.exists()
