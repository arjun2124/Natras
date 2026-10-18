"""Global market indices, commodities, currencies, and yields tracker.

Implements Section 12 (Global markets) of the NAT RAS Master Project Draft:
Candidate universe:
- Equities: S&P 500, Nasdaq 100, Dow Jones, Russell 2000, DAX, FTSE 100, Nikkei 225, Hang Seng
- Commodities: Brent Crude, WTI Crude, Gold
- Currencies: USD/INR, US Dollar Index
- Rates & Volatility: US 10-year yield, VIX
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from natras.data.session import IST
from natras.market_intelligence.source_status import SourceHealth, SourceStatus

logger = logging.getLogger(__name__)

GLOBAL_ASSETS = {
    # Equities - US
    "SP500": {"symbol": "^GSPC", "name": "S&P 500", "category": "US_EQUITY"},
    "NASDAQ100": {"symbol": "^NDX", "name": "Nasdaq 100", "category": "US_EQUITY"},
    "DOW": {"symbol": "^DJI", "name": "Dow Jones", "category": "US_EQUITY"},
    "RUSSELL2000": {"symbol": "^RUT", "name": "Russell 2000", "category": "US_EQUITY"},
    # Volatility & Rates
    "VIX": {"symbol": "^VIX", "name": "CBOE Volatility Index", "category": "VOLATILITY"},
    "US10Y": {"symbol": "^TNX", "name": "US 10-Year Treasury Yield", "category": "YIELDS"},
    # Currencies & Commodities
    "USDINR": {"symbol": "USDINR=X", "name": "USD / INR", "category": "CURRENCY"},
    "BRENT_CRUDE": {"symbol": "BZ=F", "name": "Brent Crude Oil", "category": "COMMODITY"},
    "WTI_CRUDE": {"symbol": "CL=F", "name": "WTI Crude Oil", "category": "COMMODITY"},
    "GOLD": {"symbol": "GC=F", "name": "Gold Futures", "category": "COMMODITY"},
    # Asia & Europe
    "NIKKEI225": {"symbol": "^N225", "name": "Nikkei 225", "category": "ASIA_EQUITY"},
    "HANGSENG": {"symbol": "^HSI", "name": "Hang Seng", "category": "ASIA_EQUITY"},
    "DAX": {"symbol": "^GDAXI", "name": "DAX Performance Index", "category": "EUROPE_EQUITY"},
    "FTSE100": {"symbol": "^FTSE", "name": "FTSE 100", "category": "EUROPE_EQUITY"},
}


@dataclass
class GlobalAssetQuote:
    asset_id: str
    name: str
    symbol: str
    category: str
    price: float
    change_pct: float
    prev_close: float
    updated_at: str


@dataclass
class GlobalMarketSnapshot:
    timestamp: str
    assets: Dict[str, GlobalAssetQuote]
    risk_sentiment: str  # RISK_ON, RISK_OFF, NEUTRAL
    health: SourceHealth

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["assets"] = {k: asdict(v) for k, v in self.assets.items()}
        return d


class GlobalMarketsTracker:
    """Fetches global market prices and classifies global risk sentiment."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "application/json",
        })

    def fetch_quotes(
        self,
        manual_quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> GlobalMarketSnapshot:
        """Fetch quotes from public Yahoo Finance API v8 or provided manual mock dictionary."""
        now_str = datetime.now(IST).isoformat()
        asset_quotes: Dict[str, GlobalAssetQuote] = {}
        errors: List[str] = []

        if manual_quotes:
            for k, info in GLOBAL_ASSETS.items():
                q = manual_quotes.get(k, {})
                price = float(q.get("price") or q.get("ltp") or 0.0)
                chg = float(q.get("change_pct", 0.0))
                prev = float(q.get("prev_close") or (price / (1 + chg / 100) if price and chg else price))
                asset_quotes[k] = GlobalAssetQuote(
                    asset_id=k,
                    name=info["name"],
                    symbol=info["symbol"],
                    category=info["category"],
                    price=price,
                    change_pct=chg,
                    prev_close=prev,
                    updated_at=now_str,
                )
        else:
            # Fetch batch from public quote endpoints
            for k, info in GLOBAL_ASSETS.items():
                sym = info["symbol"]
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
                try:
                    resp = self.session.get(url, timeout=self.timeout)
                    if resp.status_code == 200:
                        meta = resp.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                        price = float(meta.get("regularMarketPrice") or 0.0)
                        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
                        chg = round(((price - prev) / prev) * 100, 2) if prev > 0 else 0.0

                        asset_quotes[k] = GlobalAssetQuote(
                            asset_id=k,
                            name=info["name"],
                            symbol=sym,
                            category=info["category"],
                            price=price,
                            change_pct=chg,
                            prev_close=prev,
                            updated_at=now_str,
                        )
                    else:
                        errors.append(f"{sym}: HTTP {resp.status_code}")
                except Exception as exc:
                    errors.append(f"{sym}: {type(exc).__name__}")

        # Assess Risk Sentiment
        # Positive US equities + lower VIX = RISK_ON
        # Negative US equities + rising VIX = RISK_OFF
        sp_change = asset_quotes.get("SP500", GlobalAssetQuote("", "", "", "", 0, 0, 0, "")).change_pct
        vix_change = asset_quotes.get("VIX", GlobalAssetQuote("", "", "", "", 0, 0, 0, "")).change_pct

        if sp_change > 0.4 and vix_change < 0:
            sentiment = "RISK_ON"
        elif sp_change < -0.4 and vix_change > 0:
            sentiment = "RISK_OFF"
        else:
            sentiment = "NEUTRAL"

        status = SourceStatus.ACTIVE if len(asset_quotes) >= 5 else (SourceStatus.DEGRADED if asset_quotes else SourceStatus.UNAVAILABLE)
        health = SourceHealth(
            source_name="GlobalMarketsTracker",
            status=status,
            category="GLOBAL_MARKETS",
            last_checked=now_str,
            message=f"Retrieved {len(asset_quotes)}/{len(GLOBAL_ASSETS)} global assets. Sentiment: {sentiment}",
            error_detail="; ".join(errors[:3]) if errors else None,
        )

        return GlobalMarketSnapshot(
            timestamp=now_str,
            assets=asset_quotes,
            risk_sentiment=sentiment,
            health=health,
        )
