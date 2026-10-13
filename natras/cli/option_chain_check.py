"""Validate the real FYERS symbol master and preview strike selection.

Public data only. No credentials, no orders.

    python -m natras.cli.option_chain_check --download
    python -m natras.cli.option_chain_check --spot 25012
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from natras.options.strike_selector import (
    OptionChain,
    StrikePolicy,
    StrikeSelectionError,
    resolve_policy,
)
from natras.options.symbol_master import (
    DEFAULT_MASTER_PATH,
    IST,
    download_symbol_master,
    load_nifty_options,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", default=str(DEFAULT_MASTER_PATH))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--spot", type=float, default=None)
    parser.add_argument("--today", default=None, help="YYYY-MM-DD (IST), default today")
    args = parser.parse_args(argv)

    if args.download:
        path = download_symbol_master(args.master)
        print(f"downloaded to {path}")

    contracts, stats = load_nifty_options(args.master)
    print(
        f"rows={stats.rows_total} nifty_option_rows={stats.nifty_option_rows} "
        f"malformed={stats.malformed_rows} contracts={len(contracts)}"
    )
    try:
        chain = OptionChain(contracts)
    except StrikeSelectionError as exc:
        print(f"CHAIN ERROR: {exc}")
        return 2

    today = (
        date.fromisoformat(args.today) if args.today else datetime.now(IST).date()
    )
    expiries = chain.expiries()
    print(f"expiries (first 6): {[e.isoformat() for e in expiries[:6]]}")
    try:
        expiry = chain.nearest_expiry(today)
    except StrikeSelectionError as exc:
        print(f"EXPIRY ERROR: {exc}")
        return 2
    ce = chain.strikes(expiry, "CE")
    pe = chain.strikes(expiry, "PE")
    print(
        f"nearest expiry={expiry} lot_size={chain.lot_size(expiry)} "
        f"CE strikes={len(ce)} PE strikes={len(pe)}"
    )
    if ce:
        print(f"CE strike range {ce[0]}..{ce[-1]}")

    if args.spot is not None:
        policy = StrikePolicy()
        for option_type in ("CE", "PE"):
            try:
                result = resolve_policy(
                    chain, policy, spot=args.spot, option_type=option_type, today=today
                )
            except StrikeSelectionError as exc:
                print(f"{option_type}: ERROR {exc}")
                continue
            print(
                f"{option_type} live {result.live.moneyness.label}: "
                f"{result.live.contract.symbol}"
            )
            for label, sel in result.shadows.items():
                print(f"{option_type} shadow {label}: {sel.contract.symbol}")
            for label, err in result.shadow_errors.items():
                print(f"{option_type} shadow {label}: ERROR {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
