"""Strategy Risk Profile specification and management.

Stores risk and operational constraints independently from strategy execution code,
as mandated by Section 4 and Section 6 of the NAT RAS Master Project Draft.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
    ) -> Tuple[bool, str]:
        """Validate option quote against spread and liquidity limits.

        Quote is expected to contain:
        - 'bid' or 'b_price': best bid price
        - 'ask' or 'a_price': best ask price
        - 'ltp' or 'close': last traded price
        - 'volume' or 'vol_traded_today': volume
        """
        bid = float(quote.get("bid") or quote.get("b_price") or 0.0)
        ask = float(quote.get("ask") or quote.get("a_price") or 0.0)
        ltp = float(quote.get("ltp") or quote.get("close") or quote.get("price") or 0.0)
        vol = float(quote.get("volume") or quote.get("vol_traded_today") or 0.0)

        if ltp <= 0:
            return False, f"Invalid option LTP ({ltp:.2f}). Must be positive."

        if bid <= 0 or ask <= 0:
            # If bid or ask is missing/zero, check if spread can be determined
            return False, f"Missing or zero bid/ask in option quote (bid={bid}, ask={ask})."

        spread = round(ask - bid, 2)
        if spread < 0:
            return False, f"Inverted spread: ask ({ask}) < bid ({bid})."

        # Spread must be <= max_spread_rupees OR <= max_spread_pct_of_premium * ltp
        max_allowed_spread = max(self.max_spread_rupees, ltp * self.max_spread_pct_of_premium)
        if spread > max_allowed_spread:
            return False, (
                f"Option spread ({spread:.2f}) exceeds allowed limit ({max_allowed_spread:.2f}) "
                f"for LTP {ltp:.2f}."
            )

        if vol < self.min_contract_volume:
            return False, (
                f"Option contract volume ({vol}) below required minimum ({self.min_contract_volume})."
            )

        return True, "Option quote validated."


def get_default_risk_profile() -> StrategyRiskProfile:
    """Return the default baseline strategy risk profile."""
    return StrategyRiskProfile()
