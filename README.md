# NAT RAS

**NAT RAS** is a modular, local algorithmic trading and research platform focused strictly on the `v2.2.0-RESEARCH-MOMENTUM-WICK` strategy family.

The platform is designed with a **fail-closed, safety-first architecture**:
- **Paper Trading Only:** Live execution is disabled at both configuration and runtime levels (`LIVE_ORDERS_ENABLED = False`).
- **15:00 IST Entry Cutoff:** Strict guard prevents opening new positions at or after 15:00 Asia/Kolkata. Existing positions may continue to be managed.
- **Data Integrity:** No fabricated or synthetic market data. Completed 5-minute candles are strictly validated.
- **Audit & Journaling:** Complete persistence of orders, positions, and journal entries in local SQLite.
- **Secret Redaction:** Multi-layer secret redaction prevents API keys and tokens from appearing in logs or output.

---

## Architecture Overview

```
natras/
├── config/              # Centralized environment & safety configuration
├── logging/             # Structured logging with secret redaction
├── safety/              # Fail-closed paper enforcement & 15:00 IST entry cutoff
├── db/                  # SQLite models and persistence manager
├── risk/                # Risk governor & daily drawdown limits
├── execution/           # Paper order execution & position lifecycle
├── data/                # Strict 5-minute candle engine
├── strategies/          # Strategy registry & baseline placeholder
├── adapters/            # FYERS, Upstox, Telegram stubs (NOT_IMPLEMENTED)
├── research/            # Research scaffolding (NOT_IMPLEMENTED)
├── shadow/              # Shadow trading candidate runner (NOT_IMPLEMENTED)
└── cli/                 # CLI tools for inspection and verification
```

---

## Status of Modules (Stage 0)

| Module | Status | Notes |
|---|---|---|
| Configuration & Safety Settings | **Implemented** | Fail-closed validation for paper mode (`LIVE_ORDERS_ENABLED=False`) |
| Execution Guard (15:00 IST Cutoff) | **Implemented** | Asia/Kolkata timezone awareness; blocks entries >= 15:00 IST, permits exits |
| Secret Redaction Logging | **Implemented** | Scrubs registered secrets, Bearer tokens, and JWTs from logs |
| SQLite Persistence & Schema | **Implemented** | Orders, positions, trade journal, audit log, and candles stored with WAL mode |
| Risk Governor | **Implemented** | Daily loss circuit breaker, position sizing, and max open positions evaluated |
| Strategy Registry Framework | **Implemented** | Single strategy family lock (`v2.2.0-RESEARCH-MOMENTUM-WICK`) enforced |
| Paper Executor | **Partial** | Offline position lifecycle & SL/TP checking; no live market loop, no slippage/commission model, in-memory daily counters |
| Candle Engine | **Partial** | Completed 5-min candle validation & monotonicity; no streaming tick-to-candle builder |
| Baseline Strategy Logic | **Implemented** | `v2.2.0-RESEARCH-MOMENTUM-WICK` active: 5m spot candles, ATR(14) filter, body ratio (baseline 0.45, high-conviction 0.65), CE/PE direction, stepped trailing stop (-15 PP, +1 PP lock, peak - 3 PP trail), 09:20–14:45 window. Spec: [`docs/strategies/v2.2.0-RESEARCH-MOMENTUM-WICK.md`](docs/strategies/v2.2.0-RESEARCH-MOMENTUM-WICK.md) |
| FYERS Broker Adapter | **NOT_IMPLEMENTED** | Explicit stub raising `NotImplementedError`; no auth or streaming |
| Upstox Audit Adapter | **NOT_IMPLEMENTED** | Explicit stub raising `NotImplementedError`; no audit feed |
| Telegram Client | **NOT_IMPLEMENTED** | Explicit stub raising `NotImplementedError`; no notification dispatch |
| Research Scaffolding | **NOT_IMPLEMENTED** | Explicit stub raising `NotImplementedError` |
| Shadow Runner | **NOT_IMPLEMENTED** | Explicit stub raising `NotImplementedError` |
| Live Trading Session Loop | **NOT_IMPLEMENTED** | No daemon/runner connecting data feed, strategy, and executor |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Virtual environment (recommended)

### Setup
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Run test suite:
   ```bash
   pytest
   ```
3. Run CLI diagnostic check:
   ```bash
   python -m natras.cli.main status
   ```
