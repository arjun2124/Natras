"""Upstox Market Data Adapter (Live Provider).

Provides full integration with official Upstox Developer APIs for:
- Market Quotes (V3 LTP with ltq/volume/cp, Full Quote with 5-depth bid/ask, OHLC)
- Option Chain & Option Greeks (V3 Greeks: iv, delta, gamma, theta, vega)
- Historical Candles (V3 multi-timeframe OHLCV for Spot & Options)
- Intraday Candles (V3 real-time 1m, 5m, etc.)
- Market Intelligence (PCR, Max Pain, Open Interest, FII/DII Institutional flows)
- Smartlists & Market Timings / Status / Holidays
- Expired Derivatives Contracts & Candles (Plus Plan backtesting)
- WebSocket Feed V3 Authorization

SAFETY:
- Strictly READ-ONLY. No live order placement capabilities exist in this module.
- Fails closed: refuses live order endpoints.
- Secrets/tokens are NEVER logged, serialized, or printed.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union

import requests

from natras.cli._env import load_env

logger = logging.getLogger(__name__)

BASE_URL = "https://api.upstox.com"


class UpstoxMarketDataError(RuntimeError):
    """Raised on API errors or invalid responses from Upstox."""
    pass


class UpstoxMarketDataClient:
    """Client for Upstox Market Data & Intelligence APIs."""

    def __init__(self, token: Optional[str] = None, session: Optional[requests.Session] = None) -> None:
        load_env()
        if token is not None:
            raw_token = token
        else:
            raw_token = (
                os.environ.get("UPSTOX_ACCESS_TOKEN")
                or os.environ.get("UPSTOX_ANALYTICS_TOKEN")
                or ""
            )
        self._token = raw_token.strip()
        self._session = session or requests.Session()
        if self._token:
            self._session.headers.update({
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
            })

    def is_configured(self) -> bool:
        """Check if an access/analytics token is present."""
        return bool(self._token and "your_" not in self._token)

    def __repr__(self) -> str:
        tok_preview = (self._token[:6] + "..." + self._token[-4:]) if len(self._token) > 12 else "[NONE]"
        return f"<UpstoxMarketDataClient token={tok_preview} configured={self.is_configured()}>"

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.is_configured():
            raise UpstoxMarketDataError(
                "Upstox token not configured. Set UPSTOX_ACCESS_TOKEN or UPSTOX_ANALYTICS_TOKEN in .env"
            )
        url = f"{BASE_URL}{endpoint}"
        try:
            resp = self._session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            raise UpstoxMarketDataError(f"HTTP request failed for {endpoint}: {exc}") from exc

        if resp.status_code == 401:
            raise UpstoxMarketDataError(
                "Authentication failed (HTTP 401). Verify token validity or IP restrictions."
            )
        if resp.status_code == 429:
            raise UpstoxMarketDataError(f"Rate limited by Upstox (HTTP 429) for {endpoint}")
        if resp.status_code != 200:
            raise UpstoxMarketDataError(
                f"Upstox API returned HTTP {resp.status_code} for {endpoint}: {resp.text[:300]}"
            )

        body = resp.json()
        if body.get("status") == "error":
            errors = body.get("errors") or body.get("message")
            raise UpstoxMarketDataError(f"Upstox error response for {endpoint}: {errors}")

        return body.get("data")

    # -------------------------------------------------------------------------
    # 1. Market Quotes
    # -------------------------------------------------------------------------

    def get_ltp(self, instrument_keys: Union[str, List[str]]) -> Dict[str, Dict[str, Any]]:
        """Fetch V3 LTP quote (includes last_price, ltq, volume, cp)."""
        keys_str = ",".join(instrument_keys) if isinstance(instrument_keys, list) else instrument_keys
        return self._get("/v3/market-quote/ltp", params={"instrument_key": keys_str}) or {}

    def get_full_quote(self, instrument_keys: Union[str, List[str]]) -> Dict[str, Dict[str, Any]]:
        """Fetch full snapshot quotes with OHLC, 5-level bid/ask depth, and circuit limits."""
        keys_str = ",".join(instrument_keys) if isinstance(instrument_keys, list) else instrument_keys
        return self._get("/v2/market-quote/quotes", params={"instrument_key": keys_str}) or {}

    def get_ohlc(self, instrument_keys: Union[str, List[str]], interval: str = "1d") -> Dict[str, Dict[str, Any]]:
        """Fetch OHLC snapshot quotes for specified interval (e.g. '1d', 'I1', 'I30')."""
        keys_str = ",".join(instrument_keys) if isinstance(instrument_keys, list) else instrument_keys
        return self._get("/v2/market-quote/ohlc", params={"instrument_key": keys_str, "interval": interval}) or {}

    # -------------------------------------------------------------------------
    # 2. Options, Option Chains & Greeks
    # -------------------------------------------------------------------------

    def get_option_chain(
        self,
        underlying_key: str = "NSE_INDEX|Nifty 50",
        expiry_date: str = "current_month",
    ) -> List[Dict[str, Any]]:
        """Fetch complete option chain with strike-wise calls, puts, LTP, OI, and Greeks.

        expiry_date accepts 'YYYY-MM-DD' or keywords: 'current_week', 'next_week',
        'current_month', 'next_month'.
        """
        data = self._get("/v2/option/chain", params={"instrument_key": underlying_key, "expiry_date": expiry_date})
        return data if isinstance(data, list) else []

    def get_option_greeks(self, instrument_keys: Union[str, List[str]]) -> Dict[str, Dict[str, Any]]:
        """Fetch Option Greeks V3 (iv, delta, gamma, theta, vega, last_price, oi)."""
        keys_str = ",".join(instrument_keys) if isinstance(instrument_keys, list) else instrument_keys
        return self._get("/v3/market-quote/option-greek", params={"instrument_key": keys_str}) or {}

    def get_option_contracts(self, underlying_key: str = "NSE_INDEX|Nifty 50") -> List[Dict[str, Any]]:
        """Fetch list of all active option contracts for an underlying index/stock."""
        data = self._get("/v2/option/contract", params={"instrument_key": underlying_key})
        return data if isinstance(data, list) else []

    # -------------------------------------------------------------------------
    # 3. Historical & Intraday Candles
    # -------------------------------------------------------------------------

    def get_historical_candles(
        self,
        instrument_key: str,
        unit: str = "minutes",
        interval: Union[int, str] = 5,
        to_date: Optional[Union[str, date]] = None,
        from_date: Optional[Union[str, date]] = None,
    ) -> List[List[Any]]:
        """Fetch multi-timeframe historical OHLCV candles via Upstox V3 API.

        Format of each candle: [timestamp, open, high, low, close, volume, open_interest]
        """
        unit_str = str(unit).lower()
        if unit_str in ("day", "daily", "d"):
            unit_str = "days"
        elif unit_str in ("minute", "min", "m"):
            unit_str = "minutes"
        elif unit_str in ("week", "w"):
            unit_str = "weeks"
        elif unit_str in ("month", "mo"):
            unit_str = "months"

        inter_str = str(interval).lower().rstrip("dm")
        if unit_str in ("days", "weeks", "months") and (not inter_str or inter_str in ("day", "1d", "daily")):
            inter_str = "1"
        elif not inter_str:
            inter_str = "5"

        to_str = str(to_date or date.today())
        from_str = str(from_date) if from_date else to_str
        encoded_key = urllib.parse.quote(instrument_key)
        endpoint = f"/v3/historical-candle/{encoded_key}/{unit_str}/{inter_str}/{to_str}/{from_str}"
        data = self._get(endpoint)
        return (data or {}).get("candles", [])

    def get_intraday_candles(
        self,
        instrument_key: str,
        unit: str = "minutes",
        interval: Union[int, str] = 5,
    ) -> List[List[Any]]:
        """Fetch current trading session intraday OHLCV candles via Upstox V3 API."""
        unit_str = "minutes" if str(unit).lower() in ("minute", "min", "m", "minutes") else str(unit)
        inter_str = str(interval).lower().rstrip("m")
        encoded_key = urllib.parse.quote(instrument_key)
        endpoint = f"/v3/historical-candle/intraday/{encoded_key}/{unit_str}/{inter_str}"
        data = self._get(endpoint)
        return (data or {}).get("candles", [])

    # -------------------------------------------------------------------------
    # 4. Market Intelligence (PCR, Max Pain, OI, FII/DII)
    # -------------------------------------------------------------------------

    def get_pcr(
        self,
        instrument_key: str = "NSE_INDEX|Nifty 50",
        expiry: str = "current_month",
        date_str: Optional[Union[str, date]] = None,
        bucket_interval: int = 60,
    ) -> Dict[str, Any]:
        """Fetch Put-Call Ratio and intraday hourly insights."""
        dt = str(date_str or date.today())
        return self._get(
            "/v2/market/pcr",
            params={
                "instrument_key": instrument_key,
                "expiry": expiry,
                "date": dt,
                "bucket_interval": bucket_interval,
            },
        ) or {}

    def get_max_pain(
        self,
        instrument_key: str = "NSE_INDEX|Nifty 50",
        expiry: str = "current_month",
        date_str: Optional[Union[str, date]] = None,
        bucket_interval: int = 60,
    ) -> Dict[str, Any]:
        """Fetch Max Pain level and intraday hourly insights."""
        dt = str(date_str or date.today())
        return self._get(
            "/v2/market/max-pain",
            params={
                "instrument_key": instrument_key,
                "expiry": expiry,
                "date": dt,
                "bucket_interval": bucket_interval,
            },
        ) or {}

    def get_open_interest(
        self,
        instrument_key: str = "NSE_INDEX|Nifty 50",
        expiry: str = "current_month",
        date_str: Optional[Union[str, date]] = None,
    ) -> Dict[str, Any]:
        """Fetch strike-wise Open Interest (total calls, total puts, spot price)."""
        dt = str(date_str or date.today())
        return self._get(
            "/v2/market/oi",
            params={"instrument_key": instrument_key, "expiry": expiry, "date": dt},
        ) or {}

    def get_fii_activity(
        self,
        data_type: str = "NSE_FO|INDEX_OPTIONS",
        interval: str = "1D",
        start_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch Foreign Institutional Investor (FII) activity.

        data_type can be: NSE_FO|INDEX_OPTIONS, NSE_FO|INDEX_FUTURES,
        NSE_FO|STOCK_OPTIONS, NSE_FO|STOCK_FUTURES, NSE_EQ|CASH.
        """
        params = {"data_type": data_type, "interval": interval}
        if start_date:
            params["start_date"] = start_date
        data = self._get("/v2/market/fii", params=params)
        if isinstance(data, dict):
            return data.get(data_type, [])
        return data if isinstance(data, list) else []

    def get_dii_activity(
        self,
        data_type: str = "NSE_EQ|CASH",
        interval: str = "1D",
        start_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch Domestic Institutional Investor (DII) cash activity."""
        params = {"data_type": data_type, "interval": interval}
        if start_date:
            params["start_date"] = start_date
        data = self._get("/v2/market/dii", params=params)
        if isinstance(data, dict):
            return data.get(data_type, [])
        return data if isinstance(data, list) else []

    def get_smartlist(
        self,
        list_type: str = "options",
        asset_type: str = "INDEX",
        category: str = "MOST_ACTIVE",
    ) -> List[Dict[str, Any]]:
        """Fetch ranked Smartlists (options, futures, mtf)."""
        params = {"asset_type": asset_type, "category": category} if list_type != "mtf" else {}
        data = self._get(f"/v2/market/smartlist/{list_type}", params=params)
        if isinstance(data, dict) and "smartlist" in data:
            return data.get("smartlist", [])
        return data if isinstance(data, list) else []

    def get_news(
        self,
        category: str = "instrument_keys",
        instrument_keys: str = "NSE_INDEX|Nifty 50",
    ) -> List[Dict[str, Any]]:
        """Fetch market news articles."""
        params = {"category": category, "instrument_keys": instrument_keys}
        data = self._get("/v2/news", params=params)
        return data if isinstance(data, list) else []

    # -------------------------------------------------------------------------
    # 5. Market Information (Status, Timings, Holidays)
    # -------------------------------------------------------------------------

    def get_market_status(self, exchange: str = "NSE") -> Dict[str, Any]:
        """Check whether the exchange is OPEN, CLOSED, or in auction."""
        return self._get(f"/v2/market/status/{exchange}") or {}

    def get_market_holidays(self) -> List[Dict[str, Any]]:
        """Fetch exchange market holiday list for the year."""
        data = self._get("/v2/market/holidays")
        return data if isinstance(data, list) else []

    def get_market_timings(self, date_str: Optional[Union[str, date]] = None) -> List[Dict[str, Any]]:
        """Fetch session timings for all trading segments on a specific date."""
        dt = str(date_str or date.today())
        data = self._get(f"/v2/market/timings/{dt}")
        return data if isinstance(data, list) else []

    # -------------------------------------------------------------------------
    # 6. Expired Derivatives Backtesting (Plus Plan)
    # -------------------------------------------------------------------------

    def get_expired_option_contracts(
        self,
        underlying_key: str = "NSE_INDEX|Nifty 50",
        expiry_date: str = "2024-11-28",
    ) -> List[Dict[str, Any]]:
        """Fetch expired option contracts for historical backtesting."""
        data = self._get(
            "/v2/expired-instruments/option/contract",
            params={"instrument_key": underlying_key, "expiry_date": expiry_date},
        )
        return data if isinstance(data, list) else []

    def get_expired_candles(
        self,
        instrument_key: str,
        interval: str = "1minute",
        to_date: Optional[Union[str, date]] = None,
        from_date: Optional[Union[str, date]] = None,
    ) -> List[List[Any]]:
        """Fetch candles for expired derivative instruments."""
        to_str = str(to_date or date.today())
        from_str = str(from_date) if from_date else to_str
        encoded_key = urllib.parse.quote(instrument_key)
        endpoint = f"/v2/expired-instruments/historical-candle/{encoded_key}/{interval}/{to_str}/{from_str}"
        data = self._get(endpoint)
        return (data or {}).get("candles", [])

    # -------------------------------------------------------------------------
    # 7. Realtime WebSocket Feeds
    # -------------------------------------------------------------------------

    def get_market_data_feed_authorize(self) -> str:
        """Get authorized WebSocket feed URL for live price streaming."""
        data = self._get("/v3/feed/market-data-feed/authorize") or {}
        return data.get("authorizedRedirectUri") or data.get("authorized_redirect_uri") or ""
