"""Strategy Risk Profile specification and management.

Stores risk and operational constraints independently from strategy execution code,
as mandated by Section 4 and Section 6 of the NAT RAS Master Project Draft.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StrategyRiskProfile:
    """Risk profile for v2.2.0-RESEARCH-MOMENTUM-WICK baseline."""

    risk_profile_id: str = "v2.2.0-research-momentum-wick-default"
    strategy_id: str = "v2.2.0-RESEARCH-MOMENTUM-WICK"
    risk_profile_version: str = "1.0"

    # Capital & Sizing
    initial_paper_equity: float = 20000.0
    lot_size_units: int = 65
    min_lots: int = 1
    max_lots: int = 3
    directional_energy_min: float = 0.30

    # Option Selection & Liquidity Guardrails
    max_spread_rupees: float = 2.00
    max_spread_pct_of_premium: float = 0.05
    min_contract_volume: int = 100
    target_delta: float = 0.45
    preferred_moneyness: str = "ATM"

    # Execution Assumptions (Slippage and Brokerage)
    entry_slippage: float = 0.50
    exit_slippage: float = 0.50
    brokerage_per_order: float = 20.0

    # Stop & Trailing Rules
    initial_stop_pp: float = 15.0
    lock_in_trigger_pp: float = 4.0
    lock_in_level_pp: float = 1.0
    trail_trigger_pp: float = 8.0
    trail_gap_pp: float = 3.0
    fixed_target_pp: Optional[float] = None

    # Timing & Cutoffs (IST)
    entry_start_time: str = "09:20"
    no_new_entries_time: str = "14:45"
    hard_entry_cutoff_time: str = "15:00"
    eod_square_off_time: str = "15:00"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def validate_option_quote(
        self,
        quote: Dict[str, Any],
        current_dt: Optional[datetime] = None,
        max_staleness_seconds: float = 60.0,
    ) -> Tuple[bool, str]:
        """Validate option quote against spread, liquidity, finiteness, and staleness limits.

        Quote is expected to contain:
        - 'bid' or 'b_price': best bid price
        - 'ask' or 'a_price': best ask price
        - 'ltp' or 'close': last traded price
        - 'volume' or 'vol_traded_today': volume
        - optional timestamp: 'ts', 'timestamp', 'feed_time', or 'tt'
        """
        import math
        from datetime import timezone

        try:
            bid = float(quote.get("bid") or quote.get("b_price") or 0.0)
            ask = float(quote.get("ask") or quote.get("a_price") or 0.0)
            ltp = float(quote.get("ltp") or quote.get("close") or quote.get("price") or 0.0)
            vol = float(quote.get("volume") or quote.get("vol_traded_today") or 0.0)
        except (ValueError, TypeError) as exc:
            return False, f"Malformed quote numeric values: {exc}"

        # 1. Strict Finiteness Verification (Blocks NaN, Infinity, -Infinity)
        for name, val in [("bid", bid), ("ask", ask), ("ltp", ltp), ("volume", vol)]:
            if not math.isfinite(val):
                return False, f"Non-finite value for {name} ({val}) in option quote."

        # 2. Strict Positivity
        if ltp <= 0:
            return False, f"Invalid option LTP ({ltp:.2f}). Must be strictly positive."
        if bid <= 0 or ask <= 0:
            return False, f"Missing or non-positive bid/ask in option quote (bid={bid:.2f}, ask={ask:.2f})."
        if vol < self.min_contract_volume:
            return False, (
                f"Option contract volume ({vol:.0f}) below required minimum ({self.min_contract_volume})."
            )

        # 3. Spread Validation
        spread = round(ask - bid, 2)
        if spread < 0:
            return False, f"Inverted spread: ask ({ask}) < bid ({bid})."

        max_allowed_spread = max(self.max_spread_rupees, ltp * self.max_spread_pct_of_premium)
        if spread > max_allowed_spread:
            return False, (
                f"Option spread ({spread:.2f}) exceeds allowed limit ({max_allowed_spread:.2f}) "
                f"for LTP {ltp:.2f}."
            )

        # 4. Freshness & Staleness Verification
        ts_val = quote.get("timestamp") or quote.get("ts") or quote.get("feed_time") or quote.get("tt")
        if ts_val is not None:
            quote_dt: Optional[datetime] = None
            if isinstance(ts_val, (int, float)):
                # epoch seconds or milliseconds
                epoch_sec = ts_val if ts_val < 1e11 else ts_val / 1000.0
                quote_dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc)
            elif isinstance(ts_val, str):
                try:
                    quote_dt = datetime.fromisoformat(ts_val)
                except ValueError:
                    return False, f"Unparseable quote timestamp format: {ts_val!r}"

            if quote_dt is not None:
                # Compare against current_dt or now
                now_ref = current_dt or datetime.now(quote_dt.tzinfo or timezone.utc)
                if quote_dt.tzinfo is None and now_ref.tzinfo is not None:
                    quote_dt = quote_dt.replace(tzinfo=now_ref.tzinfo)
                elif quote_dt.tzinfo is not None and now_ref.tzinfo is None:
                    now_ref = now_ref.replace(tzinfo=quote_dt.tzinfo)

                diff = (now_ref - quote_dt).total_seconds()
                if diff < -5.0:
                    return False, f"Quote timestamp is in the future ({quote_dt.isoformat()})."
                if diff > max_staleness_seconds:
                    return False, (
                        f"Stale option quote: age ({diff:.1f}s) exceeds max allowed "
                        f"staleness ({max_staleness_seconds}s) [Quote TS: {quote_dt.isoformat()}]."
                    )

        return True, "Option quote validated."


def get_default_risk_profile() -> StrategyRiskProfile:
    """Return the default baseline strategy risk profile."""
    return StrategyRiskProfile()
