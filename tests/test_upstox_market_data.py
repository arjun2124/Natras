"""Unit tests for Upstox Market Data adapter."""

import pytest
from unittest.mock import MagicMock, patch

from natras.adapters.upstox_market_data import (
    UpstoxMarketDataClient,
    UpstoxMarketDataError,
)


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def client(mock_session):
    return UpstoxMarketDataClient(token="mock_token_abcdef123456", session=mock_session)


def test_client_init_and_repr_redaction(client):
    assert client.is_configured() is True
    rep = repr(client)
    assert "mock_t...3456" in rep
    assert "mock_token_abcdef123456" not in rep


def test_client_unconfigured():
    c = UpstoxMarketDataClient(token="")
    assert c.is_configured() is False
    with pytest.raises(UpstoxMarketDataError, match="not configured"):
        c.get_ltp("NSE_INDEX|Nifty 50")


def test_get_ltp_success(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "NSE_INDEX:Nifty 50": {
                "last_price": 22721.95,
                "instrument_token": "NSE_INDEX|Nifty 50",
                "ltq": 0,
                "volume": 0,
                "cp": 22363.35,
            }
        },
    }
    mock_session.get.return_value = mock_resp

    data = client.get_ltp(["NSE_INDEX|Nifty 50"])
    assert "NSE_INDEX:Nifty 50" in data
    assert data["NSE_INDEX:Nifty 50"]["last_price"] == 22721.95


def test_get_option_chain_success(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": [
            {
                "strike_price": 22700.0,
                "call_options": {"instrument_key": "NSE_FO|51340"},
                "put_options": {"instrument_key": "NSE_FO|51341"},
            }
        ],
    }
    mock_session.get.return_value = mock_resp

    chain = client.get_option_chain("NSE_INDEX|Nifty 50", "current_month")
    assert len(chain) == 1
    assert chain[0]["strike_price"] == 22700.0


def test_get_option_greeks_success(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "NSE_FO:NIFTY26OCT22700CE": {
                "iv": 0.14,
                "delta": 0.52,
                "gamma": 0.0012,
                "theta": -12.5,
                "vega": 18.2,
            }
        },
    }
    mock_session.get.return_value = mock_resp

    greeks = client.get_option_greeks("NSE_FO|51340")
    item = greeks["NSE_FO:NIFTY26OCT22700CE"]
    assert item["delta"] == 0.52
    assert item["iv"] == 0.14


def test_get_historical_candles_success(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-10-14T09:15:00+05:30", 22500.0, 22550.0, 22480.0, 22530.0, 10000, 0]
            ]
        },
    }
    mock_session.get.return_value = mock_resp

    candles = client.get_historical_candles("NSE_INDEX|Nifty 50", interval=5)
    assert len(candles) == 1
    assert candles[0][4] == 22530.0


def test_get_pcr_and_max_pain(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "pcr": 1.05,
            "max_pain": 23000.0,
        },
    }
    mock_session.get.return_value = mock_resp

    pcr_data = client.get_pcr("NSE_INDEX|Nifty 50", "current_month")
    assert pcr_data["pcr"] == 1.05

    pain_data = client.get_max_pain("NSE_INDEX|Nifty 50", "current_month")
    assert pain_data["max_pain"] == 23000.0


def test_get_fii_and_dii_activity(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": [{"buy_amount": 10000.0, "sell_amount": 8000.0}],
    }
    mock_session.get.return_value = mock_resp

    fii = client.get_fii_activity()
    assert len(fii) == 1
    assert fii[0]["buy_amount"] == 10000.0


def test_get_expired_contracts_and_candles(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "data": [{"instrument_key": "NSE_FO|42796|28-11-2024", "strike_price": 21600.0}],
    }
    mock_session.get.return_value = mock_resp

    contracts = client.get_expired_option_contracts("NSE_INDEX|Nifty 50", "2024-11-28")
    assert len(contracts) == 1
    assert contracts[0]["strike_price"] == 21600.0


def test_api_error_handling(client, mock_session):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_session.get.return_value = mock_resp

    with pytest.raises(UpstoxMarketDataError, match="HTTP 401"):
        client.get_ltp("NSE_INDEX|Nifty 50")

    mock_resp.status_code = 429
    with pytest.raises(UpstoxMarketDataError, match="HTTP 429"):
        client.get_ltp("NSE_INDEX|Nifty 50")

    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"
    with pytest.raises(UpstoxMarketDataError, match="HTTP 400"):
        client.get_ltp("NSE_INDEX|Nifty 50")
