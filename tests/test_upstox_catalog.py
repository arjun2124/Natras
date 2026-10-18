"""Unit tests for Upstox Catalog CLI."""

import json
from unittest.mock import MagicMock, patch

from natras.cli.upstox_catalog import (
    decode_jwt_metadata,
    print_catalog_report,
    run_catalog_verification,
)


def test_decode_jwt_metadata():
    # Construct a dummy JWT with known payload
    header = "eyJhbGciOiJIUzI1NiJ9"
    payload = "eyJzdWIiOiAiMTIzNDUiLCAiaXNQbHVzUGxhbiI6IHRydWUsICJpc0V4dGVuZGVkIjogdHJ1ZSwgImV4cCI6IDE4MjE5OTYwMDB9"
    sig = "mock_sig"
    token = f"{header}.{payload}.{sig}"

    meta = decode_jwt_metadata(token)
    assert meta["sub"] == "12345"
    assert meta["is_plus_plan"] is True
    assert meta["is_extended"] is True
    assert "2027" in meta["expires_at"]


def test_decode_jwt_metadata_invalid():
    assert decode_jwt_metadata("invalid_token") == {}


def test_run_catalog_verification_mocked():
    mock_client = MagicMock()
    mock_client.is_configured.return_value = True
    mock_client.get_full_quote.return_value = {
        "NSE_INDEX:Nifty 50": {"last_price": 22500.0, "depth": {"buy": []}}
    }
    mock_client.get_historical_candles.return_value = [[1, 2, 3, 4, 5]]
    mock_client.get_option_chain.return_value = [
        {"strike_price": 22500.0, "call_options": {"instrument_key": "KEY1"}}
    ]
    mock_client.get_option_greeks.return_value = {"KEY1": {"delta": 0.5}}
    mock_client.get_expired_option_contracts.return_value = [{"instrument_key": "EXP1"}]
    mock_client.get_expired_candles.return_value = [[1, 2, 3]]
    mock_client.get_pcr.return_value = {"pcr": 1.05, "pcr_data": [1]}
    mock_client.get_max_pain.return_value = {"max_pain": 22500.0}
    mock_client.get_fii_activity.return_value = [{"buy": 100}]
    mock_client.get_dii_activity.return_value = [{"buy": 200}]
    mock_client.get_smartlist.return_value = [{"instrument_key": "OPT1"}]
    mock_client.get_market_data_feed_authorize.return_value = "wss://feed.example.com"

    results = run_catalog_verification(mock_client)
    assert results["market_quotes"]["available"] is True
    assert results["historical_candles"]["available"] is True
    assert results["option_chain"]["strikes_count"] == 1
    assert results["option_greeks"]["available"] is True
    assert results["expired_contracts"]["available"] is True
    assert results["market_intelligence"]["pcr"] == 1.05
    assert results["institutional_flows"]["fii_records_count"] == 1
    assert results["smartlists"]["most_active_options_count"] == 1
    assert results["websocket_feed"]["available"] is True


def test_print_catalog_report_runs(capsys):
    meta = {
        "sub": "875598",
        "is_plus_plan": True,
        "is_extended": True,
        "expires_at": "2027-09-26 22:00:00 UTC",
        "issuer": "udapi-gateway-service",
    }
    catalog = {
        "historical_candles": {"available": True, "daily_sample_count": 10},
        "market_quotes": {"available": True},
        "option_chain": {"available": True, "strikes_count": 100},
        "expired_contracts": {"available": True, "nov_2024_contract_count": 50},
        "market_intelligence": {"available": True, "pcr": 1.02, "max_pain": 23000.0},
        "institutional_flows": {"available": True, "fii_records_count": 5, "dii_records_count": 5},
        "smartlists": {"available": True, "most_active_options_count": 20},
        "websocket_feed": {"available": True},
    }
    print_catalog_report(meta, catalog)
    captured = capsys.readouterr()
    assert "UPSTOX ANALYTICS TOKEN - AVAILABLE DATA INVENTORY REPORT" in captured.out
    assert "PLUS PLAN" in captured.out
    assert "HISTORICAL CANDLESTICKS:" in captured.out
