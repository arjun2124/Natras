"""SQLite persistence manager for NAT RAS."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from natras.db.models import (
    AuditLogRecord,
    CandleRecord,
    OrderRecord,
    PositionRecord,
    TradeJournalRecord,
)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT UNIQUE NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    intent TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    fill_price REAL,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT UNIQUE NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS market_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trend_regime TEXT NOT NULL,
    volatility_regime TEXT NOT NULL,
    global_sentiment TEXT NOT NULL,
    pcr REAL,
    max_pain REAL,
    vix REAL,
    fii_net_amount REAL,
    dii_net_amount REAL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    published_at TEXT NOT NULL,
    category TEXT NOT NULL,
    sector TEXT,
    headline TEXT NOT NULL,
    summary TEXT,
    importance TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS economic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    url_reference TEXT,
    publication_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    category TEXT NOT NULL,
    affected_sector TEXT,
    importance TEXT NOT NULL,
    headline TEXT NOT NULL,
    actual_value TEXT,
    previous_value TEXT,
    consensus_value TEXT,
    market_reaction TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(symbol, timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_intel_timestamp ON market_intelligence(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at);
CREATE INDEX IF NOT EXISTS idx_events_time ON economic_events(publication_time);
"""


class DatabaseManager:
    """Manages SQLite connections and queries with transactional safety."""

    def __init__(self, db_path: Path | str = "data/natras.db") -> None:
        self.db_path = Path(db_path)
        self._is_memory = str(db_path) == ":memory:" or self.db_path == Path(":memory:")
        self._mem_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def get_connection(self) -> sqlite3.Connection:
        """Obtain a connection configured for WAL mode and row factory."""
        if self._is_memory and self._mem_conn is not None:
            return self._mem_conn

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def close(self) -> None:
        """Close persistent memory connection if open."""
        if self._mem_conn:
            self._mem_conn.close()
            self._mem_conn = None

    def initialize(self) -> None:
        """Execute initial schema migration."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    # Orders API
    def save_order(self, order: OrderRecord) -> int:
        """Insert or replace an order record."""
        now_str = order.created_at or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    client_order_id, strategy_id, symbol, side, order_type,
                    intent, quantity, price, status, fill_price, created_at, executed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    status=excluded.status,
                    fill_price=excluded.fill_price,
                    executed_at=excluded.executed_at,
                    reason=excluded.reason;
                """,
                (
                    order.client_order_id,
                    order.strategy_id,
                    order.symbol,
                    order.side.value if hasattr(order.side, "value") else str(order.side),
                    order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type),
                    order.intent.value if hasattr(order.intent, "value") else str(order.intent),
                    order.quantity,
                    order.price,
                    order.status.value if hasattr(order.status, "value") else str(order.status),
                    order.fill_price,
                    now_str,
                    order.executed_at,
                    order.reason,
                ),
            )
            return cursor.lastrowid or 0

    def get_order(self, client_order_id: str) -> Optional[OrderRecord]:
        """Fetch an order by its client_order_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if not row:
                return None
            return OrderRecord(
                id=row["id"],
                client_order_id=row["client_order_id"],
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                side=row["side"],
                order_type=row["order_type"],
                intent=row["intent"],
                quantity=row["quantity"],
                price=row["price"],
                status=row["status"],
                fill_price=row["fill_price"],
                created_at=row["created_at"],
                executed_at=row["executed_at"],
                reason=row["reason"],
            )

    def get_open_orders(self, strategy_id: Optional[str] = None) -> List[OrderRecord]:
        """Fetch all pending orders."""
        query = "SELECT * FROM orders WHERE status = 'PENDING'"
        params: list[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                OrderRecord(
                    id=row["id"],
                    client_order_id=row["client_order_id"],
                    strategy_id=row["strategy_id"],
                    symbol=row["symbol"],
                    side=row["side"],
                    order_type=row["order_type"],
                    intent=row["intent"],
                    quantity=row["quantity"],
                    price=row["price"],
                    status=row["status"],
                    fill_price=row["fill_price"],
                    created_at=row["created_at"],
                    executed_at=row["executed_at"],
                    reason=row["reason"],
                )
                for row in rows
            ]

    # Positions API
    def save_position(self, pos: PositionRecord) -> int:
        """Insert or update a position record."""
        now_str = pos.opened_at or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO positions (
                    position_id, strategy_id, symbol, side, quantity,
                    entry_price, current_price, stop_loss, take_profit,
                    status, opened_at, closed_at, realized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    quantity=excluded.quantity,
                    current_price=excluded.current_price,
                    stop_loss=excluded.stop_loss,
                    take_profit=excluded.take_profit,
                    status=excluded.status,
                    closed_at=excluded.closed_at,
                    realized_pnl=excluded.realized_pnl;
                """,
                (
                    pos.position_id,
                    pos.strategy_id,
                    pos.symbol,
                    pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                    pos.quantity,
                    pos.entry_price,
                    pos.current_price,
                    pos.stop_loss,
                    pos.take_profit,
                    pos.status.value if hasattr(pos.status, "value") else str(pos.status),
                    now_str,
                    pos.closed_at,
                    pos.realized_pnl,
                ),
            )
            return cursor.lastrowid or 0

    def get_position(self, position_id: str) -> Optional[PositionRecord]:
        """Fetch a position by position_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            if not row:
                return None
            return PositionRecord(
                id=row["id"],
                position_id=row["position_id"],
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                side=row["side"],
                quantity=row["quantity"],
                entry_price=row["entry_price"],
                current_price=row["current_price"],
                stop_loss=row["stop_loss"],
                take_profit=row["take_profit"],
                status=row["status"],
                opened_at=row["opened_at"],
                closed_at=row["closed_at"],
                realized_pnl=row["realized_pnl"],
            )

    def get_open_positions(self, strategy_id: Optional[str] = None) -> List[PositionRecord]:
        """Fetch all currently open positions."""
        query = "SELECT * FROM positions WHERE status = 'OPEN'"
        params: list[Any] = []
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                PositionRecord(
                    id=row["id"],
                    position_id=row["position_id"],
                    strategy_id=row["strategy_id"],
                    symbol=row["symbol"],
                    side=row["side"],
                    quantity=row["quantity"],
                    entry_price=row["entry_price"],
                    current_price=row["current_price"],
                    stop_loss=row["stop_loss"],
                    take_profit=row["take_profit"],
                    status=row["status"],
                    opened_at=row["opened_at"],
                    closed_at=row["closed_at"],
                    realized_pnl=row["realized_pnl"],
                )
                for row in rows
            ]

    # Journal & Audit API
    def log_journal(self, entry: TradeJournalRecord) -> int:
        """Insert a trade journal record."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trade_journal (
                    timestamp, event_type, strategy_id, symbol, payload_json, message
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp,
                    entry.event_type,
                    entry.strategy_id,
                    entry.symbol,
                    entry.payload_json,
                    entry.message,
                ),
            )
            return cursor.lastrowid or 0

    def get_journal_entries(self, limit: int = 100) -> List[TradeJournalRecord]:
        """Retrieve recent trade journal entries."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_journal ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                TradeJournalRecord(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    event_type=row["event_type"],
                    strategy_id=row["strategy_id"],
                    symbol=row["symbol"],
                    payload_json=row["payload_json"],
                    message=row["message"],
                )
                for row in rows
            ]

    def log_audit(self, entry: AuditLogRecord) -> int:
        """Insert an audit log record."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, category, level, message, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp,
                    entry.category,
                    entry.level,
                    entry.message,
                    entry.details_json,
                ),
            )
            return cursor.lastrowid or 0

    # Candle Persistence
    def save_candle(self, c: CandleRecord) -> None:
        """Store a completed candle."""
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO candles (
                    symbol, timeframe, timestamp, open, high, low, close, volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume;
                """,
                (
                    c.symbol,
                    c.timeframe,
                    c.timestamp,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                ),
            )

    def get_recent_candles(self, symbol: str, timeframe: int = 5, limit: int = 50) -> List[CandleRecord]:
        """Fetch recent completed candles ordered chronologically."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume
                FROM (
                    SELECT * FROM candles
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ) ORDER BY timestamp ASC
                """,
                (symbol, timeframe, limit),
            ).fetchall()
            return [
                CandleRecord(
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    timestamp=row["timestamp"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                )
                for row in rows
            ]

    # Market Intelligence API
    def save_market_intelligence_snapshot(self, snapshot: Dict[str, Any]) -> int:
        """Persist a consolidated market intelligence snapshot."""
        now_iso = snapshot.get("timestamp") or datetime.now(timezone.utc).isoformat()
        regime = snapshot.get("regime") or {}
        pcr = snapshot.get("pcr")
        max_pain = snapshot.get("max_pain")
        vix = snapshot.get("vix")
        fii = snapshot.get("fii_net_amount")
        dii = snapshot.get("dii_net_amount")
        payload = json.dumps(snapshot)

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO market_intelligence (
                    timestamp, trend_regime, volatility_regime, global_sentiment,
                    pcr, max_pain, vix, fii_net_amount, dii_net_amount, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso,
                    regime.get("trend_regime", "UNKNOWN"),
                    regime.get("volatility_regime", "NORMAL"),
                    regime.get("global_sentiment", "NEUTRAL"),
                    pcr,
                    max_pain,
                    vix,
                    fii,
                    dii,
                    payload,
                ),
            )
            return cursor.lastrowid

    def get_latest_market_intelligence(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recent market intelligence snapshot."""
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM market_intelligence
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if row:
                try:
                    return json.loads(row["payload_json"])
                except Exception:
                    return None
            return None

    # News & Macro Events API
    def save_news_item(self, item: Dict[str, Any]) -> None:
        """Insert or ignore a news item."""
        retrieved_at = item.get("retrieved_at") or datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO news_items (
                    news_id, source, url, published_at, category, sector,
                    headline, summary, importance, retrieved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("news_id", ""),
                    item.get("source", ""),
                    item.get("url", ""),
                    item.get("published_at", ""),
                    item.get("category", ""),
                    item.get("sector", ""),
                    item.get("headline", ""),
                    item.get("summary", ""),
                    item.get("importance", "MEDIUM"),
                    retrieved_at,
                ),
            )

    def get_recent_news_items(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent news items ordered by publish date."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT news_id, source, url, published_at, category, sector,
                       headline, summary, importance, retrieved_at
                FROM news_items
                ORDER BY published_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_economic_event(self, event: Dict[str, Any]) -> None:
        """Insert or replace a scheduled economic event."""
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO economic_events (
                    event_id, source, url_reference, publication_time, timezone,
                    category, affected_sector, importance, headline,
                    actual_value, previous_value, consensus_value, market_reaction
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    actual_value=excluded.actual_value,
                    previous_value=excluded.previous_value,
                    market_reaction=excluded.market_reaction;
                """,
                (
                    event.get("event_id", ""),
                    event.get("source", ""),
                    event.get("url_reference", ""),
                    event.get("publication_time", ""),
                    event.get("timezone", "Asia/Kolkata"),
                    event.get("category", ""),
                    event.get("affected_sector", ""),
                    event.get("importance", "MEDIUM"),
                    event.get("headline", ""),
                    event.get("actual_value"),
                    event.get("previous_value"),
                    event.get("consensus_value"),
                    event.get("market_reaction"),
                ),
            )

    def get_upcoming_economic_events(self, since_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve upcoming economic events."""
        dt_str = since_date or datetime.now(timezone.utc).isoformat()[:10]
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM economic_events
                WHERE publication_time >= ?
                ORDER BY publication_time ASC
                """,
                (dt_str,),
            ).fetchall()
            return [dict(row) for row in rows]
