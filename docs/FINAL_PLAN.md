# NAT RAS — approved operating plan

Status: **approved project direction**. This document turns the agreed chat plan into a repository source of truth. It does not alter the safety mandates in `GEMINI.md`; when there is a conflict, `GEMINI.md` wins.

## Objective

NAT RAS is a modular local system for **one strategy family only**: `v2.2.0-RESEARCH-MOMENTUM-WICK` (Intraday Bar and Wick Momentum). The objective is to operationalize its baseline in paper/shadow trading, collect evidence, run controlled candidate research, and progress toward live readiness without compromising safety.

This is not an unrelated-strategy generator. Every candidate remains an explicitly versioned descendant of the baseline, for example `v2.2.1` and `v2.2.2`; the active baseline is never overwritten automatically.

## Non-negotiable safety

- Strictly paper/shadow only. `LIVE_ORDERS_ENABLED` must stay `false`; invalid or true values fail closed.
- No code path may place a live broker order.
- Never fabricate market prices, candles, Greeks, IV, OI, fills, symbols, or unavailable values.
- All timestamps and session decisions use `Asia/Kolkata`.
- Strategy entry window is 09:20–14:45 IST. Platform hard guard blocks new entries at or after 15:00 IST. Existing positions may be managed and square-off actions are permitted after 15:00 IST.
- Broker symbols are read verbatim from the broker/master data; never constructed by guesswork.
- Secrets exist only in local `.env`; they are redacted from logs and never committed.

## Runtime path

```text
Existing strategy
  -> live/historical data foundation
  -> baseline paper trading
  -> controlled research and enhancement
  -> shadow candidates
  -> Telegram approval
  -> next-session paper promotion
  -> paper-to-live validation
  -> live trading (only after separate approval and proof)
```

The first live-paper run must do the following:

1. Authenticate to FYERS locally.
2. Receive genuine NIFTY spot data and generate completed five-minute candles only.
3. Evaluate the confirmed Momentum Wick baseline on completed candles.
4. Resolve a listed NIFTY option contract from the daily FYERS master: nearest listed weekly expiry, dynamic ATM live selection, and ITM1/OTM1 research shadows.
5. Create paper positions only; no broker order submission.
6. Monitor genuine option premiums to operate the existing stepped trailing stop.
7. Record signals, rejections, contract resolution, fills, stop changes, exits, data health, and shadow comparisons.
8. Send Telegram status and approval messages.
9. Fail visibly if necessary data is stale, incomplete, invalid, or unavailable.

## Instrument policy

- Underlying: NIFTY 50 index options only for the present phase.
- Expiry: nearest listed expiry from the current daily master; do not infer a weekday or construct contracts.
- Live paper selection: ATM by default.
- Research shadows: ITM1 and OTM1 by default, on the same data/cost assumptions.
- Direction: baseline bullish signal maps to CE; bearish maps to PE.
- Lot size, strike grid, tick size, expiry and symbols come from the symbol master. The current 65 lot size is only a hint, not a hard-coded source of truth.

## Data provenance

Every persisted field must include a source classification:

| Classification | Meaning |
|---|---|
| `BROKER_HISTORICAL` | Broker-provided historical response |
| `BROKER_LIVE` | Broker-provided live response |
| `PUBLIC_SOURCE` | Public exchange or other public source |
| `CALCULATED` | Deterministically calculated from retained inputs |
| `UNAVAILABLE` | Requested but unavailable; no substitute may be invented |

Raw API responses are retained where legally and operationally practical, with retrieval time, instrument, interval and request parameters sufficient to reproduce normalisation. The dataset manifest records source, date/contract coverage, interval, response status, checksums where practical, gaps, duplicates and resumability status.

## Three parallel tracks

### 1. Historical options and spot collection

Start with an Upstox capability audit and the user-provided temporary token. The downloader must:

- Validate token and account/API capability without logging credentials.
- Discover NIFTY spot instruments, option expiries, strikes and CE/PE contracts.
- Test historical and expired-option access rather than assuming it.
- Download the maximum complete accessible NIFTY spot/options dataset by contract, date and interval.
- Request five-minute option and spot candles where available.
- Retain returned volume and OI; test IV/Greeks availability separately.
- Persist raw payloads, normalized records, a manifest, gaps, duplicates and retry/resume state.
- Respect documented API limits and plan restrictions.

"Full historical options data" means the maximum complete data that the accessible Upstox entitlement, endpoints, intervals and contracts actually provide. It must never be claimed if unavailable.

### 2. Market intelligence

Initial collectors are observational. They classify and record data; they do **not** silently turn headlines into entry signals or override baseline rules.

Target sources/categories:

- Indian market: NIFTY, Bank NIFTY, relevant indices, NIFTY 50 constituents, sectors, breadth, advance/decline, volume, India VIX, FII/DII, option-chain/OI/change OI/PCR/max pain/expiry, corporate and economic events.
- Global context: GIFT NIFTY, major US, Asian and European indices, VIX, DXY, USD/INR, US 10Y, Brent/WTI, gold.
- Events/news: RBI, Fed, inflation, GDP, PMI, IIP, trade, employment, central-bank decisions, commodities/currency/geopolitics, constituent and sector announcements, earnings.

Any use of a public value must retain its URL, retrieval time and provenance. A public/exchange reference is not treated as an execution price unless explicitly supported and labeled.

### 3. Research, enhancement and shadows

Research begins with the confirmed baseline and changes one interpretable dimension at a time. It may test:

- ATR/range and body-ratio thresholds, wick behavior and entry timing.
- Hard stop, lock-in and trailing parameters.
- Option delta, ATM versus ITM/OTM, expiry selection, spread/volume constraints.
- Time-of-day, regime, global-context, IV, OI and PCR filters.
- Position sizing, slippage and brokerage assumptions.

Research sequence:

1. Verify baseline implementation, option resolution, entry timing and stop behavior.
2. Verify cost/slippage assumptions and time-of-day performance.
3. Test option-selection changes.
4. Test stop/trailing changes.
5. Test individual market-regime/context filters.
6. Test combinations only after isolated effects are understood.

All experiments declare exact configuration, data range, missing-data policy, cost assumptions, metrics and an out-of-sample/forward-paper validation requirement. A result without adequate data is not a conclusion.

## Shadow candidates

Candidates may be created automatically, but are paper/shadow only. Each uses:

- The same real market data, candle definitions, entry cutoff, cost/slippage assumptions and paper-account assumptions as the baseline.
- An independent virtual position ledger and immutable run configuration.
- A candidate identifier, baseline identifier, data-quality state and reason for each decision.

Removal occurs after 3–5 **valid** trading sessions only when the candidate had adequate comparable opportunity/data and does not improve the baseline. It is marked:

- `INSUFFICIENT_DATA` if valid opportunities or feed completeness are inadequate.
- `REMOVED_NO_IMPROVEMENT_3_TO_5_SESSIONS` if adequately tested without improvement.

Stopping a runtime candidate preserves a compact audit record; it does not erase its evidence.

## Promotion

A candidate that meets declared evaluation criteria creates a Telegram approval request containing baseline/candidate versions, data coverage, assumptions, metrics, limitations and proposed change.

- Approval in session `N` may activate the candidate **only in session `N+1`**.
- No candidate replaces the active strategy during the same session.
- Approval promotes only to paper operation. Any real-money transition requires a separate later decision, operational validation and explicit user approval.

## Staged delivery

### Tomorrow: start, not completion

Before market open:

- Validate FYERS and Upstox access.
- Start NIFTY spot/option discovery and historical jobs.
- Start market-intelligence collectors.
- Prepare baseline, research and shadow configuration.
- Start the five-minute candle service.

During market hours:

- Use genuine FYERS data; run baseline paper evaluation.
- Run approved candidates in shadow mode when their data requirements are met.
- Enforce cutoffs, manage paper positions, record all signals/rejections and collect available option data/context.

After close:

- Continue downloads, normalize records, identify gaps/duplicates, replay the baseline, run initial experiments, compare candidates, update shadow set and send Telegram reports.

### Honest expectations

By tomorrow night, feasible targets are validated connections, started download jobs, initial genuine data, paper signal wiring, initial intelligence collectors, research/shadow foundations and data-quality status.

Not provable in one day: complete historical coverage, a reliable optimization conclusion, a statistically superior candidate or a live-ready strategy. Those require several valid sessions and out-of-sample validation.

## Build order

1. FYERS data foundation — merged as PR #1.
2. NIFTY symbol-master/ATM-ITM-OTM resolver — merged as PR #2.
3. Paper runtime: contract binding, option premium feed, stepped-stop dispatcher, session orchestrator and forced EOD handling.
4. Persistence/telemetry: journal, data health and Telegram alerts.
5. Upstox audit and resumable historical collection.
6. Market-intelligence collectors and normalized context model.
7. Research/replay engine, candidate registry and independent shadow ledgers.
8. Telegram approval workflow and next-session-only paper promotion.
9. Multi-session reliability and evidence review before any live-trading consideration.

## Acceptance criteria before live consideration

- Multiple complete paper sessions with verified FYERS data, candles, contract resolution and trailing-stop behavior.
- No violation of paper-only/cutoff/secret/data-integrity safeguards.
- Known data coverage and gap reports; no false claim of unavailable historical coverage.
- Candidate changes reproducible with declared data, assumptions and out-of-sample validation.
- Explicit user approval for every promotion and a separate explicit decision before live trading.
