# FYERS feed (data only)

Status: IMPLEMENTED_UNVERIFIED_LIVE. Unit-tested with fakes; not yet verified against a live FYERS session. No order placement exists anywhere in this module.

## Setup

1. `pip install fyers-apiv3`
2. Put these in your local `.env` (never commit): `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI`. Align the names with `.env.example` if they differ.
3. Run `python -m natras.cli.fyers_login` once per trading day.

## Modules

- `natras/data/session.py`: IST session helpers.
- `natras/data/tick_aggregator.py`: ticks to completed 5-minute candles. Gaps are recorded, never filled.
- `natras/adapters/fyers_auth.py`: auth-code flow and token store.
- `natras/adapters/fyers_live.py`: quotes, history and the WebSocket feed.

## Known limits to verify live

- Token expiry (~06:00 IST) is an assumption.
- First candle after a (re)connect may under-count volume (unknown baseline).
- Exchange holidays are not modelled.
- Candle timestamp is the bucket start time.
