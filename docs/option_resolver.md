# NIFTY option resolver

Status: `IMPLEMENTED_UNVERIFIED_LIVE`. Parser tested against real symbol master rows pasted from the downloaded file; selection logic tested with fixtures. Data only: no orders, no credentials.

## Symbol master

Source: `https://public.fyers.in/sym_details/NSE_FO.csv` (public, no header row, 21 columns). Confirmed layout (0-indexed):

| Col | Field |
|---|---|
| 1 | description |
| 2 | instrument type (`14` option, `11` future) |
| 3 | lot size |
| 4 | tick size |
| 8 | expiry, epoch seconds (15:40 IST on expiry day) |
| 9 | FYERS ticker, used verbatim |
| 13 | underlying (`NIFTY` exact match) |
| 15 | strike |
| 16 | option type |

Only rows with underlying exactly `NIFTY`, type `14`, and `CE`/`PE` are kept. BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 and futures are excluded. Symbols are never constructed.

## Selection rules

- Expiry: nearest listed expiry on or after today (IST). Default `min_days_to_expiry=0` includes expiry day itself; the 14:45 strategy cutoff and 15:00 platform cutoff still apply elsewhere.
- ATM: existing strike closest to spot; ties go to the lower strike. If the chain does not cover spot within `max_atm_gap` (default 100 points), selection fails.
- ITM/OTM: N listed strikes away from ATM. CE ITM is lower, CE OTM is higher; PE ITM is higher, PE OTM is lower.
- Lot size: read from the master. A different value from the 65 hint only logs a warning. Inconsistent lot sizes within an expiry raise an error.
- Missing contracts raise `StrikeSelectionError`. Nothing is invented.

## Policy

`StrikePolicy` defaults to live `ATM` with shadow `ITM1` and `OTM1` for research comparison. Shadow failures are recorded, never raised. Live failures propagate.

## Validate on your machine

```
python -m natras.cli.option_chain_check --download
python -m natras.cli.option_chain_check --spot <current NIFTY spot>
```

The downloaded file lands in the git-ignored `data/` folder.

## Not in this PR

Option premium ticks, the trailing stop dispatcher, and the session orchestrator (PR #3).
