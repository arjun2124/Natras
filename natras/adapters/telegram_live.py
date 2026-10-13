"""Telegram Bot client and command dispatcher for NAT RAS.

Implements Section 11 of the NAT RAS Master Project Draft:
- Notifications for lifecycle, data feed, orders, stops, and PnL.
- Interactive command handling (/status, /auth_status, /feed_status, /paper_status,
  /open_positions, /today_report, /strategy_status, /errors, /help).
- Strict secret redaction: tokens, secrets, and auth headers are never transmitted.
- Only authorized user (TELEGRAM_AUTHORIZED_USER_ID or TELEGRAM_CHAT_ID) is responded to.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from natras.adapters.fyers_auth import FyersAuthenticator, FyersCredentials
from natras.config.constants import MARKET_TIMEZONE_STR, STRATEGY_FAMILY
from natras.config.settings import load_settings
from natras.data.session import IST
from natras.db.database import DatabaseManager
from natras.logging.redaction import redact_string
from natras.safety.guards import SafetyGuard

logger = logging.getLogger(__name__)

DB_PATH = Path("data") / "market.db"
LOG_DIR = Path("data") / "logs"


class TelegramLiveBot:
    """Telegram bot client supporting notifications and polling for commands."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        db_manager: Optional[DatabaseManager] = None,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_AUTHORIZED_USER_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id and "your_" not in self.token and "your_" not in self.chat_id)
        self.db = db_manager or DatabaseManager()
        self._last_update_id: Optional[int] = None
        self._secrets: Optional[set[str]] = None

    def _get_secrets(self) -> set[str]:
        if self._secrets is None:
            settings = load_settings()
            self._secrets = settings.get_secrets()
        return self._secrets

    def send_message(self, text: str) -> bool:
        """Send a message to the authorized chat with secret redaction."""
        if not self.enabled:
            logger.warning("Telegram bot not configured; message not sent.")
            return False

        clean_text = redact_string(text, custom_secrets=self._get_secrets())
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": clean_text,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            logger.error("Telegram API error: HTTP %s %s", resp.status_code, resp.text[:200])
            return False
        except requests.RequestException as exc:
            logger.error("Failed to send Telegram message: %s", type(exc).__name__)
            return False

    def handle_command(self, text: str) -> str:
        """Parse and execute a Telegram command."""
        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if "@" in cmd:
            cmd = cmd.split("@")[0]

        if cmd in ("/status", "/start"):
            return self._cmd_status()
        elif cmd == "/auth_status":
            return self._cmd_auth_status()
        elif cmd == "/feed_status":
            return self._cmd_feed_status()
        elif cmd == "/paper_status":
            return self._cmd_paper_status()
        elif cmd == "/open_positions":
            return self._cmd_open_positions()
        elif cmd == "/today_report":
            return self._cmd_today_report()
        elif cmd == "/strategy_status":
            return self._cmd_strategy_status()
        elif cmd in ("/market_intel", "/regime"):
            return self._cmd_market_intel()
        elif cmd == "/shadow_status":
            return self._cmd_shadow_status()
        elif cmd == "/promotion_requests":
            return self._cmd_promotion_requests()
        elif cmd == "/approve":
            return self._cmd_approve(arg)
        elif cmd == "/reject":
            return self._cmd_reject(arg)
        elif cmd == "/errors":
            return self._cmd_errors()
        elif cmd == "/help":
            return self._cmd_help()
        else:
            return f"Unknown command '{cmd}'. Type /help to see available commands."

    def _cmd_status(self) -> str:
        settings = load_settings()
        guard = SafetyGuard(settings)
        now_ist = datetime.now(ZoneInfo(MARKET_TIMEZONE_STR))
        allowed, reason = guard.is_entry_allowed(now_ist)

        return (
            "📊 *NAT RAS SYSTEM STATUS*\n"
            f"• Mode: PAPER ONLY (Fail-Closed)\n"
            f"• Live Orders: {settings.live_orders_enabled} (BLOCKED)\n"
            f"• IST Time: {now_ist.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Entry Cutoff (15:00): {'ALLOWED' if allowed else 'BLOCKED'}\n"
            f"• Strategy Family: {STRATEGY_FAMILY}\n"
            f"• Database: {settings.database_path.name}\n"
            f"• Cutoff Status: {reason}"
        )

    def _cmd_auth_status(self) -> str:
        fyers_status = "NOT CONFIGURED"
        try:
            creds = FyersCredentials.from_env()
            auth = FyersAuthenticator(creds)
            auth.get_access_token()
            fyers_status = "VALID (Session active)"
        except Exception as exc:
            fyers_status = f"INACTIVE ({type(exc).__name__})"

        upstox_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        upstox_status = "CONFIGURED" if bool(upstox_token and "your_" not in upstox_token) else "NOT SET"

        return (
            "🔑 *AUTHENTICATION STATUS*\n"
            f"• FYERS Auth: {fyers_status}\n"
            f"• Upstox Token: {upstox_status}\n"
            "• Secrets Protected: Redacted in logs & output"
        )

    def _cmd_feed_status(self) -> str:
        try:
            if not DB_PATH.exists():
                return "📡 *FEED STATUS*\n• Market DB not created yet."
            conn = sqlite3.connect(DB_PATH)
            today = datetime.now(IST).date().isoformat()
            row = conn.execute(
                "SELECT COUNT(*), MAX(ts) FROM candles WHERE ts LIKE ?",
                (today + "%",),
            ).fetchone()
            conn.close()
            count = row[0] if row else 0
            last_ts = row[1] if row and row[1] else "None"
            return (
                "📡 *FEED STATUS*\n"
                f"• Today's Candles Saved: {count}\n"
                f"• Last Candle TS: {last_ts}\n"
                f"• Status: {'OK' if count > 0 else 'Awaiting market open'}"
            )
        except Exception as exc:
            return f"📡 *FEED STATUS*\n• Error reading database: {exc}"

    def _cmd_paper_status(self) -> str:
        open_pos = self.db.get_open_positions()
        journal = self.db.get_journal_entries(limit=50)
        fills_today = sum(1 for j in journal if j.event_type == "ORDER_FILLED")

        # Sum realized PnL from closed positions today
        today_str = datetime.now(IST).date().isoformat()
        realized_today = 0.0
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE closed_at LIKE ?",
                (today_str + "%",),
            ).fetchone()
            if row:
                realized_today = float(row[0])

        return (
            "📝 *PAPER TRADING STATUS*\n"
            f"• Open Positions: {len(open_pos)}\n"
            f"• Orders Executed Today: {fills_today}\n"
            f"• Realized PnL Today: ₹{realized_today:.2f}\n"
            "• Execution Mode: Paper with Slippage (₹0.50) & Brokerage (₹20)"
        )

    def _cmd_open_positions(self) -> str:
        open_pos = self.db.get_open_positions()
        if not open_pos:
            return "📋 *OPEN POSITIONS*\nNo active positions."

        lines = ["📋 *OPEN POSITIONS*"]
        for p in open_pos:
            lines.append(
                f"• ID: {p.position_id}\n"
                f"  Symbol: {p.symbol}\n"
                f"  Side: {p.side} | Qty: {p.quantity}\n"
                f"  Entry: ₹{p.entry_price:.2f} | Current: ₹{p.current_price:.2f}\n"
                f"  SL: ₹{p.stop_loss if p.stop_loss else 'None'}"
            )
        return "\n\n".join(lines)

    def _cmd_today_report(self) -> str:
        today_str = datetime.now(IST).date().isoformat()
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT position_id, symbol, side, quantity, entry_price, current_price, realized_pnl, closed_at "
                "FROM positions WHERE closed_at LIKE ?",
                (today_str + "%",),
            ).fetchall()

        trades_count = len(rows)
        net_pnl = sum(r["realized_pnl"] for r in rows)
        wins = sum(1 for r in rows if r["realized_pnl"] > 0)
        losses = sum(1 for r in rows if r["realized_pnl"] < 0)

        return (
            "📈 *TODAY'S TRADING REPORT*\n"
            f"• Date: {today_str}\n"
            f"• Closed Trades: {trades_count}\n"
            f"• Wins: {wins} | Losses: {losses}\n"
            f"• Net Realized PnL: ₹{net_pnl:.2f}\n"
            f"• Brokerage Applied: ₹{trades_count * 40.0:.2f}"
        )

    def _cmd_strategy_status(self) -> str:
        from natras.strategies.momentum_wick import MomentumWickStrategy

        strat = MomentumWickStrategy()
        st = strat.get_status()
        return (
            "🎯 *STRATEGY STATUS*\n"
            f"• Strategy ID: {st['strategy_id']}\n"
            f"• Name: {st['canonical_name']} (v{st['version']})\n"
            f"• Status: {st['status']}\n"
            f"• Window: {st['parameter_defaults']['entry_start_time']} - "
            f"{st['parameter_defaults']['no_new_entries_time']} IST\n"
            f"• Trailing Stop: -{st['parameter_defaults']['initial_stop_pp']} PP initial, "
            f"+{st['parameter_defaults']['lock_in_trigger_pp']} PP lock, "
            f"+{st['parameter_defaults']['trail_trigger_pp']} PP trail\n"
            f"• History Candles Loaded: {st['history_candles']}\n"
            f"• Last Rejection: {st['last_rejection_reason'] or 'None'}"
        )

    def _cmd_errors(self) -> str:
        log_file = LOG_DIR / "autostart.log"
        if not log_file.exists():
            return "⚠️ *ERRORS LOG*\nNo autostart log found."
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            errors = [l for l in lines if "ERROR" in l or "WARNING" in l][-5:]
            if not errors:
                return "✅ *ERRORS LOG*\nNo recent errors or warnings found."
            return "⚠️ *RECENT LOG EVENTS*\n" + "\n".join(errors)
        except Exception as exc:
            return f"Failed to read logs: {exc}"

    def _cmd_market_intel(self) -> str:
        from natras.market_intelligence.orchestrator import MarketIntelligenceOrchestrator

        try:
            orchestrator = MarketIntelligenceOrchestrator()
            report = orchestrator.generate_report()
            reg = report.regime
            events_txt = ""
            if report.upcoming_events:
                events_txt = f"\n• Next Event: {report.upcoming_events[0]['headline'][:50]}"

            return (
                "🌐 *MARKET INTELLIGENCE & REGIME*\n"
                f"• Trend Regime: {reg['trend_regime']}\n"
                f"• Volatility: {reg['volatility_regime']}\n"
                f"• Global Sentiment: {reg['global_sentiment']}\n"
                f"• Confidence: {reg['confidence_score']:.0%}\n"
                f"• Event Risk: {'HIGH' if reg['is_event_day'] else 'NORMAL'}"
                f"{events_txt}\n\n"
                f"_{reg['summary_text']}_"
            )
        except Exception as exc:
            return f"Market intelligence generation error: {exc}"

    def _cmd_shadow_status(self) -> str:
        try:
            from natras.shadow.shadow_engine import ShadowEngine
            engine = ShadowEngine()
            active = engine.candidates
            if not active:
                return "👥 *SHADOW STRATEGIES*\nNo shadow variants currently active."
            lines = [f"👥 *SHADOW STRATEGIES ({len(active)})*"]
            for c in active.values():
                lines.append(
                    f"• ID: `{c.candidate_id}`\n"
                    f"  Name: {c.name}\n"
                    f"  Sessions: {c.sessions_evaluated} | Trades: {c.trade_count} (Wins: {c.win_count})\n"
                    f"  Net PnL: Rs {c.net_pnl:,.2f} | PF: {c.profit_factor:.2f}"
                )
            return "\n\n".join(lines)
        except Exception as exc:
            return f"Shadow status error: {exc}"

    def _cmd_promotion_requests(self) -> str:
        try:
            from natras.promotion.promotion_manager import PromotionManager
            mgr = PromotionManager()
            pending = mgr.pending_activations
            if not pending:
                return "🚀 *PROMOTION REQUESTS*\nNo candidates pending activation."
            lines = [f"🚀 *PENDING ACTIVATIONS ({len(pending)})*"]
            for p in pending.values():
                lines.append(
                    f"• Candidate: `{p.candidate_id}`\n"
                    f"  Approved By: {p.approved_by} at {p.approved_at}\n"
                    f"  Scheduled: {p.scheduled_activation_session}"
                )
            return "\n\n".join(lines)
        except Exception as exc:
            return f"Promotion requests error: {exc}"

    def _cmd_approve(self, candidate_id: str) -> str:
        if not candidate_id:
            return "Usage: `/approve <candidate_id>`"
        try:
            from natras.promotion.promotion_manager import PromotionManager
            from natras.shadow.shadow_engine import ShadowEngine
            engine = ShadowEngine()
            mgr = PromotionManager()
            candidate = engine.candidates.get(candidate_id)
            if not candidate:
                return f"❌ Candidate `{candidate_id}` not found in shadow registry."
            record = mgr.approve_candidate(candidate, approved_by=f"Telegram ({self.chat_id})")
            return (
                f"✅ *STRATEGY PROMOTION APPROVED*\n"
                f"• Candidate: `{record.candidate_id}`\n"
                f"• Activation: {record.scheduled_activation_session} (N+1 session)\n"
                f"• Current Active: `{record.previous_strategy_id}` continues until end-of-session."
            )
        except Exception as exc:
            return f"Approval error: {exc}"

    def _cmd_reject(self, candidate_id: str) -> str:
        if not candidate_id:
            return "Usage: `/reject <candidate_id>`"
        try:
            from natras.promotion.promotion_manager import PromotionManager
            mgr = PromotionManager()
            if candidate_id in mgr.pending_activations:
                mgr.pending_activations.pop(candidate_id, None)
                mgr._save()
                return f"🚫 Candidate `{candidate_id}` rejected and removed from pending activations."
            return f"Candidate `{candidate_id}` was not pending activation."
        except Exception as exc:
            return f"Rejection error: {exc}"

    def _cmd_help(self) -> str:
        return (
            "🤖 *NAT RAS TELEGRAM COMMANDS*\n\n"
            "/status - System readiness, time, cutoff status\n"
            "/auth_status - FYERS and Upstox token validity\n"
            "/feed_status - Live candle feed status and counts\n"
            "/paper_status - Paper trading state and executed orders\n"
            "/open_positions - Active open positions details\n"
            "/today_report - Today's closed trades and net PnL\n"
            "/strategy_status - Baseline strategy parameters & health\n"
            "/market_intel - Market regime, global sentiment, macro events\n"
            "/shadow_status - Live shadow candidate variants and metrics\n"
            "/promotion_requests - Pending strategy promotions for next session\n"
            "/approve <id> - Approve shadow candidate for N+1 session\n"
            "/reject <id> - Reject pending candidate\n"
            "/errors - Recent errors and warnings\n"
            "/help - Display this commands list"
        )

    def poll_once(self) -> int:
        """Poll getUpdates once, dispatch commands, and return count of handled messages."""
        if not self.enabled:
            return 0

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: Dict[str, Any] = {"timeout": 1}
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return 0
            data = resp.json()
        except (requests.RequestException, ValueError):
            return 0

        handled = 0
        for upd in data.get("result", []):
            self._last_update_id = upd["update_id"]
            msg = upd.get("message") or {}
            sender_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")

            # Security: Only process commands from the authorized user/chat
            if sender_id != str(self.chat_id):
                logger.warning("Ignored message from unauthorized sender %s", sender_id)
                continue

            if text.startswith("/"):
                response = self.handle_command(text)
                self.send_message(response)
                handled += 1

        return handled
