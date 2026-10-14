"""Indian markets breadth and sectoral relative strength analysis.

Implements Section 12 (Indian markets) of the NAT RAS Master Project Draft:
- NIFTY 50 & sectoral indices (Bank Nifty, IT, Auto, Pharma, Metal, FMCG, Energy)
- Market breadth (advances, declines, advance/decline ratio)
- Sector relative strength against NIFTY 50 benchmark
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from natras.data.session import IST
from natras.market_intelligence.source_status import SourceHealth, SourceStatus

SECTOR_SYMBOLS = {
    "NIFTY_BANK": "NSE:NIFTYBANK-INDEX",
    "NIFTY_IT": "NSE:NIFTYIT-INDEX",
    "NIFTY_AUTO": "NSE:NIFTYAUTO-INDEX",
    "NIFTY_PHARMA": "NSE:NIFTYPHARMA-INDEX",
    "NIFTY_FMCG": "NSE:NIFTYFMCG-INDEX",
    "NIFTY_METAL": "NSE:NIFTYMETAL-INDEX",
    "NIFTY_ENERGY": "NSE:NIFTYENERGY-INDEX",
    "NIFTY_FIN_SERVICE": "NSE:NIFTYFINSERVICE-INDEX",
}

NIFTY50_CONSTITUENTS = [
    "NSE:ADANIENT-EQ", "NSE:ADANIPORTS-EQ", "NSE:APOLLOHOSP-EQ", "NSE:ASIANPAINT-EQ", "NSE:AXISBANK-EQ",
    "NSE:BAJAJ-AUTO-EQ", "NSE:BAJFINANCE-EQ", "NSE:BAJAJFINSV-EQ", "NSE:BEL-EQ", "NSE:BPCL-EQ",
    "NSE:BHARTIARTL-EQ", "NSE:BRITANNIA-EQ", "NSE:CIPLA-EQ", "NSE:COALINDIA-EQ", "NSE:DRREDDY-EQ",
    "NSE:EICHERMOT-EQ", "NSE:GRASIM-EQ", "NSE:HCLTECH-EQ", "NSE:HDFCBANK-EQ", "NSE:HDFCLIFE-EQ",
    "NSE:HEROMOTOCO-EQ", "NSE:HINDALCO-EQ", "NSE:HINDUNILVR-EQ", "NSE:ICICIBANK-EQ", "NSE:ITC-EQ",
    "NSE:INDUSINDBK-EQ", "NSE:INFY-EQ", "NSE:JSWSTEEL-EQ", "NSE:KOTAKBANK-EQ", "NSE:LT-EQ",
    "NSE:M&M-EQ", "NSE:MARUTI-EQ", "NSE:NTPC-EQ", "NSE:NESTLEIND-EQ", "NSE:ONGC-EQ",
    "NSE:POWERGRID-EQ", "NSE:RELIANCE-EQ", "NSE:SBILIFE-EQ", "NSE:SHRIRAMFIN-EQ", "NSE:SBIN-EQ",
    "NSE:SUNPHARMA-EQ", "NSE:TCS-EQ", "NSE:TATACONSUM-EQ", "NSE:TATAMOTORS-EQ", "NSE:TATASTEEL-EQ",
    "NSE:TECHM-EQ", "NSE:TITAN-EQ", "NSE:TRENT-EQ", "NSE:ULTRACEMCO-EQ", "NSE:WIPRO-EQ",
]


@dataclass
class SectorPerformance:
    sector: str
    symbol: str
    ltp: float
    change_pct: float
    relative_strength: float  # Sector return minus benchmark return


@dataclass
class MarketBreadthSnapshot:
    timestamp: str
    benchmark_symbol: str
    benchmark_change_pct: float
    advances: int
    declines: int
    unchanged: int
    advance_decline_ratio: float
    sectors: List[SectorPerformance]
    top_outperforming_sector: Optional[str] = None
    top_underperforming_sector: Optional[str] = None
    health: Optional[SourceHealth] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IndiaMarketBreadthAnalyzer:
    """Computes advance/decline and sectoral rotation metrics."""

    def analyze(
        self,
        nifty_quote: Dict[str, Any],
        sector_quotes: Dict[str, Dict[str, Any]],
        constituent_quotes: Optional[List[Dict[str, Any]]] = None,
    ) -> MarketBreadthSnapshot:
        """Analyze Indian market breadth using real quotes from broker feed or API."""
        now_str = datetime.now(IST).isoformat()

        # Benchmark return
        nifty_ltp = float(nifty_quote.get("lp") or nifty_quote.get("ltp") or nifty_quote.get("close") or 0.0)
        nifty_prev = float(nifty_quote.get("prev_close_price") or nifty_quote.get("prev_close") or nifty_quote.get("open") or nifty_ltp)
        nifty_change_pct = round(((nifty_ltp - nifty_prev) / nifty_prev) * 100, 2) if nifty_prev > 0 else 0.0

        sectors: List[SectorPerformance] = []
        advances = declines = unchanged = 0

        # Sectoral performance
        for sec_name, sym in SECTOR_SYMBOLS.items():
            q = sector_quotes.get(sym, {})
            ltp = float(q.get("lp") or q.get("ltp") or q.get("close") or 0.0)
            prev = float(q.get("prev_close_price") or q.get("prev_close") or q.get("open") or ltp)
            chg = round(((ltp - prev) / prev) * 100, 2) if prev > 0 else 0.0
            rs = round(chg - nifty_change_pct, 2)

            if chg > 0.05:
                advances += 1
            elif chg < -0.05:
                declines += 1
            else:
                unchanged += 1

            sectors.append(SectorPerformance(
                sector=sec_name,
                symbol=sym,
                ltp=ltp,
                change_pct=chg,
                relative_strength=rs,
            ))

        # If constituent quotes provided, count stock breadth across Nifty 50 stocks
        if constituent_quotes:
            c_adv = 0
            c_dec = 0
            c_unc = 0
            for c in constituent_quotes:
                if "change_pct" in c:
                    chg = float(c["change_pct"])
                else:
                    c_ltp = float(c.get("lp") or c.get("ltp") or c.get("close") or 0.0)
                    c_prev = float(c.get("prev_close_price") or c.get("prev_close") or c.get("open") or c_ltp)
                    chg = round(((c_ltp - c_prev) / c_prev) * 100, 2) if c_prev > 0 else 0.0
                if chg > 0.05:
                    c_adv += 1
                elif chg < -0.05:
                    c_dec += 1
                else:
                    c_unc += 1
            advances, declines, unchanged = c_adv, c_dec, c_unc

        total = advances + declines + unchanged
        ad_ratio = round(advances / declines, 2) if declines > 0 else (float(advances) if advances > 0 else 1.0)

        # Outperforming / underperforming sectors
        sorted_sectors = sorted(sectors, key=lambda s: s.relative_strength, reverse=True)
        top_out = sorted_sectors[0].sector if sorted_sectors else None
        top_under = sorted_sectors[-1].sector if sorted_sectors else None

        status = SourceStatus.ACTIVE if (nifty_ltp > 0 and len(sectors) > 0) else SourceStatus.DEGRADED
        health = SourceHealth(
            source_name="IndianMarketBreadth",
            status=status,
            category="INDIAN_MARKETS",
            last_checked=now_str,
            message=f"Analyzed {len(sectors)} sectors. A/D: {advances}/{declines}",
        )

        return MarketBreadthSnapshot(
            timestamp=now_str,
            benchmark_symbol="NSE:NIFTY50-INDEX",
            benchmark_change_pct=nifty_change_pct,
            advances=advances,
            declines=declines,
            unchanged=unchanged,
            advance_decline_ratio=ad_ratio,
            sectors=sectors,
            top_outperforming_sector=top_out,
            top_underperforming_sector=top_under,
            health=health,
        )
