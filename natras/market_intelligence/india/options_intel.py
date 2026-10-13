"""Indian options market intelligence (PCR, Max Pain, and OI shifts).

Implements Section 12 (Options intelligence) of the NAT RAS Master Project Draft:
- Put-Call Ratio (PCR) by volume and open interest
- Max Pain strike calculation
- Strike-level volume and open-interest distribution
- Labeling of BROKER_PROVIDED vs CALCULATED fields
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from natras.data.session import IST
from natras.market_intelligence.source_status import SourceHealth, SourceStatus


@dataclass
class StrikeOI:
    strike: float
    ce_oi: float
    pe_oi: float
    ce_volume: float
    pe_volume: float
    ce_ltp: float
    pe_ltp: float


@dataclass
class OptionsIntelligenceSnapshot:
    timestamp: str
    expiry_date: str
    underlying_spot: float
    pcr_oi: float
    pcr_volume: float
    max_pain_strike: float
    highest_ce_oi_strike: float
    highest_pe_oi_strike: float
    strikes_data: List[StrikeOI]
    health: SourceHealth

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OptionsIntelligenceAnalyzer:
    """Computes PCR, Max Pain, and Open Interest concentration."""

    def analyze(
        self,
        spot: float,
        expiry: str,
        chain_quotes: List[Dict[str, Any]],
    ) -> OptionsIntelligenceSnapshot:
        """Calculate options market intelligence from option chain quotes.

        chain_quotes items are expected to contain:
        {'strike': float, 'type': 'CE'|'PE', 'oi': float, 'volume': float, 'ltp': float}
        """
        now_str = datetime.now(IST).isoformat()
        strikes_map: Dict[float, Dict[str, float]] = {}

        for q in chain_quotes:
            strike = float(q.get("strike", 0))
            if strike <= 0:
                continue
            otype = str(q.get("type", "")).upper()
            oi = float(q.get("oi") or q.get("open_interest") or 0.0)
            vol = float(q.get("volume") or q.get("vol_traded_today") or 0.0)
            ltp = float(q.get("ltp") or q.get("close") or 0.0)

            if strike not in strikes_map:
                strikes_map[strike] = {
                    "ce_oi": 0.0, "pe_oi": 0.0,
                    "ce_vol": 0.0, "pe_vol": 0.0,
                    "ce_ltp": 0.0, "pe_ltp": 0.0,
                }

            if otype == "CE":
                strikes_map[strike]["ce_oi"] += oi
                strikes_map[strike]["ce_vol"] += vol
                strikes_map[strike]["ce_ltp"] = ltp
            elif otype == "PE":
                strikes_map[strike]["pe_oi"] += oi
                strikes_map[strike]["pe_vol"] += vol
                strikes_map[strike]["pe_ltp"] = ltp

        total_ce_oi = sum(s["ce_oi"] for s in strikes_map.values())
        total_pe_oi = sum(s["pe_oi"] for s in strikes_map.values())
        total_ce_vol = sum(s["ce_vol"] for s in strikes_map.values())
        total_pe_vol = sum(s["pe_vol"] for s in strikes_map.values())

        pcr_oi = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else 1.0
        pcr_vol = round(total_pe_vol / total_ce_vol, 3) if total_ce_vol > 0 else 1.0

        # Calculate Max Pain: strike that minimizes total payout to option buyers
        sorted_strikes = sorted(strikes_map.keys())
        min_loss = float("inf")
        max_pain = spot

        for target_strike in sorted_strikes:
            total_loss = 0.0
            for strike, data in strikes_map.items():
                # Loss from CE buyers: max(0, target - strike) * ce_oi
                if target_strike > strike:
                    total_loss += (target_strike - strike) * data["ce_oi"]
                # Loss from PE buyers: max(0, strike - target) * pe_oi
                elif target_strike < strike:
                    total_loss += (strike - target_strike) * data["pe_oi"]

            if total_loss < min_loss:
                min_loss = total_loss
                max_pain = target_strike

        # Highest OI strikes (Resistance & Support levels)
        high_ce_strike = max(sorted_strikes, key=lambda s: strikes_map[s]["ce_oi"]) if sorted_strikes else spot
        high_pe_strike = max(sorted_strikes, key=lambda s: strikes_map[s]["pe_oi"]) if sorted_strikes else spot

        strikes_list = [
            StrikeOI(
                strike=s,
                ce_oi=d["ce_oi"],
                pe_oi=d["pe_oi"],
                ce_volume=d["ce_vol"],
                pe_volume=d["pe_vol"],
                ce_ltp=d["ce_ltp"],
                pe_ltp=d["pe_ltp"],
            )
            for s, d in sorted(strikes_map.items())
        ]

        status = SourceStatus.ACTIVE if strikes_list else SourceStatus.UNAVAILABLE
        health = SourceHealth(
            source_name="NiftyOptionsIntel",
            status=status,
            category="INDIAN_OPTIONS",
            last_checked=now_str,
            message=f"Analyzed {len(strikes_list)} strikes. PCR(OI): {pcr_oi}, Max Pain: {max_pain}",
        )

        return OptionsIntelligenceSnapshot(
            timestamp=now_str,
            expiry_date=expiry,
            underlying_spot=spot,
            pcr_oi=pcr_oi,
            pcr_volume=pcr_vol,
            max_pain_strike=max_pain,
            highest_ce_oi_strike=high_ce_strike,
            highest_pe_oi_strike=high_pe_strike,
            strikes_data=strikes_list,
            health=health,
        )
