"""Nearest-expiry and dynamic ATM / ITM / OTM strike selection for NIFTY options.

ITM / OTM are counted in strikes away from ATM using the strikes that actually
exist in the symbol master (never an assumed step size):

    CE: ITM = lower strike,  OTM = higher strike
    PE: ITM = higher strike, OTM = lower strike

If a requested contract does not exist, an error is raised. Nothing is invented.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from natras.options.symbol_master import NIFTY_LOT_SIZE_HINT, OptionContract

log = logging.getLogger(__name__)

_MONEYNESS_RE = re.compile(r"ATM|(ITM|OTM)(\d+)")


class StrikeSelectionError(ValueError):
    """Raised when no valid contract can be selected."""


@dataclass(frozen=True)
class Moneyness:
    kind: str
    steps: int = 0

    def __post_init__(self) -> None:
        if self.kind == "ATM":
            if self.steps != 0:
                raise StrikeSelectionError("ATM takes no steps")
        elif self.kind in ("ITM", "OTM"):
            if self.steps < 1:
                raise StrikeSelectionError(f"{self.kind} needs steps >= 1")
        else:
            raise StrikeSelectionError(f"unknown moneyness kind: {self.kind!r}")

    @classmethod
    def parse(cls, text: str) -> "Moneyness":
        match = _MONEYNESS_RE.fullmatch(text.strip().upper())
        if match is None:
            raise StrikeSelectionError(f"cannot parse moneyness: {text!r}")
        if match.group(1) is None:
            return cls("ATM")
        return cls(match.group(1), int(match.group(2)))

    @property
    def label(self) -> str:
        return "ATM" if self.kind == "ATM" else f"{self.kind}{self.steps}"


class OptionChain:
    """Index of NIFTY option contracts by expiry, option type and strike."""

    def __init__(self, contracts: Iterable[OptionContract]) -> None:
        self._by_key: dict[tuple[date, str, float], OptionContract] = {}
        self._lots: dict[date, set[int]] = {}
        for contract in contracts:
            key = (contract.expiry_date, contract.option_type, contract.strike)
            previous = self._by_key.get(key)
            if previous is not None and previous.symbol != contract.symbol:
                raise StrikeSelectionError(
                    f"conflicting contracts for {key}: "
                    f"{previous.symbol} vs {contract.symbol}"
                )
            self._by_key[key] = contract
            self._lots.setdefault(contract.expiry_date, set()).add(contract.lot_size)
        self._strikes: dict[tuple[date, str], list[float]] = {}
        for expiry, option_type, strike in self._by_key:
            self._strikes.setdefault((expiry, option_type), []).append(strike)
        for values in self._strikes.values():
            values.sort()

    def expiries(self) -> list[date]:
        return sorted(self._lots)

    def nearest_expiry(self, today: date, min_days_to_expiry: int = 0) -> date:
        candidates = [
            e for e in self._lots if (e - today).days >= min_days_to_expiry
        ]
        if not candidates:
            raise StrikeSelectionError(
                f"no expiry on or after {today} (min_days={min_days_to_expiry})"
            )
        return min(candidates)

    def strikes(self, expiry: date, option_type: str) -> list[float]:
        return list(self._strikes.get((expiry, option_type), []))

    def contract(
        self, expiry: date, option_type: str, strike: float
    ) -> OptionContract:
        try:
            return self._by_key[(expiry, option_type, strike)]
        except KeyError:
            raise StrikeSelectionError(
                f"no contract for {expiry} {strike} {option_type}"
            ) from None

    def lot_size(self, expiry: date) -> int:
        sizes = self._lots.get(expiry)
        if not sizes:
            raise StrikeSelectionError(f"no contracts for expiry {expiry}")
        if len(sizes) > 1:
            raise StrikeSelectionError(
                f"inconsistent lot sizes for {expiry}: {sorted(sizes)}"
            )
        size = next(iter(sizes))
        if size != NIFTY_LOT_SIZE_HINT:
            log.warning(
                "NIFTY lot size in master is %s, expected hint %s; using master",
                size,
                NIFTY_LOT_SIZE_HINT,
            )
        return size


def calculate_black_scholes_delta(
    spot: float,
    strike: float,
    days_to_expiry: float,
    option_type: str,
    iv: float = 0.15,
    r: float = 0.065,
) -> float:
    """Calculate standard Black-Scholes option delta.

    Returns:
    - CE: delta in [0.0, 1.0]
    - PE: delta in [-1.0, 0.0]
    """
    if not math.isfinite(spot) or spot <= 0 or not math.isfinite(strike) or strike <= 0:
        return 0.50 if option_type == "CE" else -0.50

    t = max(days_to_expiry, 0.5) / 365.0
    sigma = max(iv, 0.05)
    sqrt_t = math.sqrt(t)

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    norm_cdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))

    if option_type == "CE":
        return max(0.0, min(1.0, norm_cdf))
    else:
        return max(-1.0, min(0.0, norm_cdf - 1.0))


@dataclass(frozen=True)
class Selection:
    contract: OptionContract
    expiry: date
    atm_strike: float
    moneyness: Moneyness
    spot: float
    lot_size: int
    estimated_delta: float = 0.50


def select_contract(
    chain: OptionChain,
    *,
    spot: float,
    option_type: str,
    moneyness: Moneyness,
    today: date,
    min_days_to_expiry: int = 0,
    max_atm_gap: float = 100.0,
) -> Selection:
    """Select one contract for the nearest expiry.

    ATM is the existing strike closest to spot (ties go to the lower strike).
    If ATM is further than ``max_atm_gap`` index points from spot, the chain is
    treated as unusable and an error is raised.
    """
    if option_type not in ("CE", "PE"):
        raise StrikeSelectionError(f"option_type must be CE or PE, got {option_type!r}")
    if not math.isfinite(spot) or spot <= 0:
        raise StrikeSelectionError(f"invalid spot: {spot!r}")

    expiry = chain.nearest_expiry(today, min_days_to_expiry)
    strikes = chain.strikes(expiry, option_type)
    if not strikes:
        raise StrikeSelectionError(f"no {option_type} strikes for {expiry}")

    atm = min(strikes, key=lambda s: (abs(s - spot), s))
    if abs(atm - spot) > max_atm_gap:
        raise StrikeSelectionError(
            f"spot {spot} is {abs(atm - spot):.1f} points from nearest strike "
            f"{atm}; chain does not cover spot"
        )
    index = strikes.index(atm)

    if moneyness.kind == "ATM":
        shift = 0
    else:
        toward_lower = (moneyness.kind == "ITM") == (option_type == "CE")
        shift = -moneyness.steps if toward_lower else moneyness.steps

    target = index + shift
    if not 0 <= target < len(strikes):
        raise StrikeSelectionError(
            f"{moneyness.label} {option_type} is outside the listed strikes "
            f"for {expiry}"
        )
    chosen_strike = strikes[target]
    contract = chain.contract(expiry, option_type, chosen_strike)
    days_to_exp = max(0, (expiry - today).days)
    delta = round(calculate_black_scholes_delta(spot, chosen_strike, days_to_exp, option_type), 4)

    return Selection(
        contract=contract,
        expiry=expiry,
        atm_strike=atm,
        moneyness=moneyness,
        spot=spot,
        lot_size=chain.lot_size(expiry),
        estimated_delta=delta,
    )


def select_contract_by_delta(
    chain: OptionChain,
    *,
    spot: float,
    option_type: str,
    target_delta: float = 0.45,
    delta_tolerance: float = 0.15,
    today: date,
    min_days_to_expiry: int = 0,
    max_atm_gap: float = 100.0,
) -> Selection:
    """Select the contract whose estimated Black-Scholes delta is closest to target_delta.

    Enforces Master Draft Section 4 requirement: 'Target delta approximately 0.45'.
    """
    if option_type not in ("CE", "PE"):
        raise StrikeSelectionError(f"option_type must be CE or PE, got {option_type!r}")
    if not math.isfinite(spot) or spot <= 0:
        raise StrikeSelectionError(f"invalid spot: {spot!r}")

    expiry = chain.nearest_expiry(today, min_days_to_expiry)
    strikes = chain.strikes(expiry, option_type)
    if not strikes:
        raise StrikeSelectionError(f"no {option_type} strikes for {expiry}")

    days_to_exp = max(0, (expiry - today).days)
    target_mag = abs(target_delta)

    # Compute delta for each strike and find closest
    candidates = []
    for s in strikes:
        d = calculate_black_scholes_delta(spot, s, days_to_exp, option_type)
        diff = abs(abs(d) - target_mag)
        candidates.append((diff, s, d))

    candidates.sort(key=lambda x: (x[0], abs(x[1] - spot)))
    best_diff, best_strike, best_delta = candidates[0]

    if best_diff > delta_tolerance:
        raise StrikeSelectionError(
            f"No {option_type} strike within delta tolerance {delta_tolerance:.2f} of target {target_delta:.2f}. "
            f"Best was strike {best_strike} with delta {best_delta:.4f} (diff {best_diff:.4f})."
        )

    atm = min(strikes, key=lambda s: (abs(s - spot), s))
    atm_idx = strikes.index(atm)
    chosen_idx = strikes.index(best_strike)
    steps = abs(chosen_idx - atm_idx)
    if steps == 0:
        moneyness = Moneyness("ATM")
    else:
        is_itm = (chosen_idx < atm_idx) if option_type == "CE" else (chosen_idx > atm_idx)
        moneyness = Moneyness("ITM" if is_itm else "OTM", steps)

    contract = chain.contract(expiry, option_type, best_strike)
    return Selection(
        contract=contract,
        expiry=expiry,
        atm_strike=atm,
        moneyness=moneyness,
        spot=spot,
        lot_size=chain.lot_size(expiry),
        estimated_delta=round(best_delta, 4),
    )


@dataclass(frozen=True)
class StrikePolicy:
    """Live moneyness plus shadow moneynesses recorded for research only."""

    live: Moneyness = Moneyness("ATM")
    shadows: tuple[Moneyness, ...] = (Moneyness("ITM", 1), Moneyness("OTM", 1))


@dataclass(frozen=True)
class PolicyResult:
    live: Selection
    shadows: dict[str, Selection] = field(default_factory=dict)
    shadow_errors: dict[str, str] = field(default_factory=dict)


def resolve_policy(
    chain: OptionChain,
    policy: StrikePolicy,
    *,
    spot: float,
    option_type: str,
    today: date,
    min_days_to_expiry: int = 0,
    max_atm_gap: float = 100.0,
) -> PolicyResult:
    """Resolve the live contract (errors propagate) and shadow contracts (errors recorded)."""
    kwargs = dict(
        spot=spot,
        option_type=option_type,
        today=today,
        min_days_to_expiry=min_days_to_expiry,
        max_atm_gap=max_atm_gap,
    )
    live = select_contract(chain, moneyness=policy.live, **kwargs)
    shadows: dict[str, Selection] = {}
    errors: dict[str, str] = {}
    for moneyness in policy.shadows:
        try:
            shadows[moneyness.label] = select_contract(
                chain, moneyness=moneyness, **kwargs
            )
        except StrikeSelectionError as exc:
            errors[moneyness.label] = str(exc)
    return PolicyResult(live=live, shadows=shadows, shadow_errors=errors)
