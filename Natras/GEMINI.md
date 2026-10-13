# GEMINI.md - Agent Instructions & Project Mandates

This document serves as the governing ruleset for all AI agents and contributors working in this repository.

## 1. Project Overview & Identity
- **Project Name:** NAT RAS
- **Scope:** Modular local algorithmic-trading and research platform focused strictly on one strategy family: `v2.2.0-RESEARCH-MOMENTUM-WICK`.
- **Primary Objective:** Operationalize the strategy in paper/shadow trading, conduct rigorous controlled research, and progress towards live readiness without ever compromising safety.
- **Current Mode:** Strictly Paper/Shadow Trading only.

---

## 2. Non-Negotiable Safety Mandates
1. **Paper-Only Fail-Closed:**
   - `LIVE_ORDERS_ENABLED` must remain `false`.
   - The platform must fail-closed if `LIVE_ORDERS_ENABLED` is set to `true` or invalid. Startup or execution must abort immediately.
   - Under no circumstances may any code path place live broker orders.
2. **15:00 IST Entry Cutoff:**
   - Market Timezone: `Asia/Kolkata`.
   - No new position may be opened at or after 15:00 IST.
   - Existing open positions may continue to be managed after 15:00 IST (stop-loss, trailing stop, take-profit, or emergency square-off are allowed).
3. **No Synthetic / Fabricated Market Data:**
   - Never fabricate market prices, candles, Greeks, IV, or option data.
   - If market data is unavailable or disconnected, fail clearly. Never mock synthetic random fills in production paths.
4. **Honest Implementation Labeling:**
   - If a broker adapter, audit provider, or feature is not implemented, label it explicitly as `NOT_IMPLEMENTED` and raise `NotImplementedError`. Never fake or simulate external connectivity.
5. **Secret Redaction & Protection:**
   - Secrets (API keys, secrets, access tokens, auth headers, passwords) must live ONLY in local environment configuration (`.env`).
   - Secrets must NEVER appear in source code, commit history, documentation, tests, or log outputs.
   - All loggers must pass through automated secret redaction before writing to console or file.
6. **Workspace Boundaries:**
   - All edits must happen strictly inside this local repository directory.
   - Do not use scratch directories outside this workspace for project code.
   - Do not touch external trading directories or legacy projects.
   - Do not create a git repository or remote unless explicitly requested.

---

## 3. Architecture Boundaries & Module Responsibilities

```
natras/
├── config/         # System settings, environment loader, constants, secret keys list
├── logging/        # Logger setup, automated secret redaction filter & formatter
├── safety/         # Execution guards: paper-only fail-closed check, 15:00 IST cutoff
├── db/             # SQLite connection manager, schema, models (orders, positions, journal)
├── risk/           # Risk governor: max drawdown, position sizing, order limits
├── execution/      # Paper trading engine: fill simulation, position lifecycle, PnL
├── data/           # Completed 5-minute candle engine and strict OHLC validators
├── strategies/     # BaseStrategy interface, StrategyRegistry, baseline placeholder
├── adapters/       # Broker & service stubs: FYERS, Upstox, Telegram (labeled NOT_IMPLEMENTED)
├── research/       # Research scaffolding stubs (labeled NOT_IMPLEMENTED)
├── shadow/         # Shadow candidate runner stubs (labeled NOT_IMPLEMENTED)
└── cli/            # Local CLI inspection & diagnostic tools
```

---

## 4. The "NEVER DO" List

- **NEVER** set `LIVE_ORDERS_ENABLED = True`.
- **NEVER** allow an entry order at or after 15:00 IST.
- **NEVER** invent, fabricate, or improvise strategy rules for `v2.2.0-RESEARCH-MOMENTUM-WICK`. The baseline strategy must remain a placeholder until the user explicitly provides or confirms the ported source code.
- **NEVER** fabricate OHLC data, Greeks, or fake prices.
- **NEVER** print or log API keys, access tokens, or sensitive user secrets.
- **NEVER** delete files without explaining the reason and receiving user confirmation.
- **NEVER** claim a broker integration is complete when it is only a stub or partially mocked.

---

## 5. Coding & Workflow Standards
- **Python Version:** Python 3.11+.
- **Standard Library First:** Utilize Python's standard library (`sqlite3`, `zoneinfo`, `dataclasses`, `logging`, `pathlib`) for core safety and persistence.
- **Timezone Awareness:** All trading time checks must explicitly use `ZoneInfo("Asia/Kolkata")`.
- **Validation Before Mutation:** Validate all incoming data (candles, orders, configurations) against strict domain models before updating system state.

---

## 6. Testing & Verification Checklist
Before declaring any task or phase complete, verify:
- [ ] All unit tests pass: `pytest tests/`
- [ ] Paper-only guard fails closed if `LIVE_ORDERS_ENABLED` is tampered with.
- [ ] 15:00 IST entry cutoff blocks entries at 15:00:00 and beyond.
- [ ] 15:00 IST entry cutoff permits position exits and stop updates.
- [ ] Redactor strips tokens, keys, and passwords from logs.
- [ ] Baseline strategy placeholder refuses to invent trading rules.
- [ ] Incomplete external integrations explicitly raise `NotImplementedError`.
- [ ] SQLite database schema initializes cleanly and stores orders, positions, and journal entries.
- [ ] No secrets or sensitive keys are present in files or tests.
