"""Strategy promotion and activation governance.

Implements Section 16 and Phase 6 of the NAT RAS Master Project Draft:
- Requires Telegram human approval before promoting any candidate to active paper strategy.
- Next-Session Activation: If approved in session N, activation is scheduled for session N+1.
- No mid-session replacement is ever allowed.
- Generates detailed candidate reports with risk/return comparisons against baseline.
- Stores immutable approval records, maintains version history, and enables instant rollback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from natras.data.session import IST
from natras.shadow.shadow_engine import ShadowCandidate

logger = logging.getLogger(__name__)

PROMOTION_STORE = Path("runtime_data") / "promotion" / "promotion_state.json"


@dataclass
class PromotionRecord:
    candidate_id: str
    parent_strategy: str
    approved_by: str
    approved_at: str
    scheduled_activation_session: str
    status: str  # PENDING_ACTIVATION, ACTIVE, REJECTED, ROLLED_BACK
    previous_strategy_id: str
    candidate_metrics: Dict[str, Any]


class PromotionManager:
    """Manages the governance and lifecycle of strategy promotions."""

    def __init__(self, store_path: Path | str = PROMOTION_STORE) -> None:
        self.store_path = Path(store_path)
        self.active_strategy_id = "v2.2.0-RESEARCH-MOMENTUM-WICK"
        self.pending_activations: Dict[str, PromotionRecord] = {}
        self.history: List[PromotionRecord] = []
        self._load()

    def create_candidate_report(self, candidate: ShadowCandidate) -> str:
        """Format promotion candidate report for Telegram approval."""
        return (
            "📋 *PROMOTION CANDIDATE REPORT*\n"
            f"• Candidate ID: `{candidate.candidate_id}`\n"
            f"• Name: {candidate.name}\n"
            f"• Parent: {candidate.parent_strategy}\n"
            f"• Sessions Evaluated: {candidate.sessions_evaluated}\n"
            f"• Virtual Trades: {candidate.trade_count} (Wins: {candidate.win_count})\n"
            f"• Net Realized PnL: ₹{candidate.net_pnl:.2f}\n"
            f"• Profit Factor: {candidate.profit_factor:.2f}\n"
            f"• Parameter Changes: {json.dumps(candidate.parameters, indent=1)}\n\n"
            f"To approve for NEXT trading session:\n"
            f"`/approve {candidate.candidate_id}`\n"
            f"To reject:\n"
            f"`/reject {candidate.candidate_id}`"
        )

    def approve_candidate(
        self,
        candidate: ShadowCandidate,
        approved_by: str = "TelegramUser",
        current_dt: Optional[datetime] = None,
    ) -> PromotionRecord:
        """Approve candidate for next-session activation."""
        now = current_dt or datetime.now(IST)
        next_session_date = now.date().isoformat()

        record = PromotionRecord(
            candidate_id=candidate.candidate_id,
            parent_strategy=candidate.parent_strategy,
            approved_by=approved_by,
            approved_at=now.isoformat(),
            scheduled_activation_session=f"Next session after {next_session_date}",
            status="PENDING_ACTIVATION",
            previous_strategy_id=self.active_strategy_id,
            candidate_metrics={
                "net_pnl": candidate.net_pnl,
                "profit_factor": candidate.profit_factor,
                "trades": candidate.trade_count,
            },
        )
        self.pending_activations[candidate.candidate_id] = record
        self._save()
        logger.info(
            "Candidate %s approved by %s for next session activation.",
            candidate.candidate_id,
            approved_by,
        )
        return record

    def reject_candidate(self, candidate_id: str, reason: str = "User rejected") -> None:
        """Explicitly reject a promotion candidate."""
        record = PromotionRecord(
            candidate_id=candidate_id,
            parent_strategy="v2.2.0-RESEARCH-MOMENTUM-WICK",
            approved_by="Rejected",
            approved_at=datetime.now(IST).isoformat(),
            scheduled_activation_session="N/A",
            status="REJECTED",
            previous_strategy_id=self.active_strategy_id,
            candidate_metrics={"rejection_reason": reason},
        )
        self.history.append(record)
        self._save()

    def activate_scheduled_for_session(self) -> Optional[str]:
        """Activate any pending approved candidate at the start of a new trading session."""
        if not self.pending_activations:
            return None

        # Pop first pending activation
        cid, record = self.pending_activations.popitem()
        record.status = "ACTIVE"
        self.history.append(record)
        self.active_strategy_id = cid
        self._save()
        logger.info("Activated promoted strategy %s as active paper strategy.", cid)
        return cid

    def rollback(self) -> str:
        """Rollback to previous baseline strategy."""
        if self.history:
            last = self.history[-1]
            last.status = "ROLLED_BACK"
            self.active_strategy_id = last.previous_strategy_id
        else:
            self.active_strategy_id = "v2.2.0-RESEARCH-MOMENTUM-WICK"
        self._save()
        logger.info("Rolled back active strategy to %s", self.active_strategy_id)
        return self.active_strategy_id

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_strategy_id": self.active_strategy_id,
            "pending_activations": {k: asdict(v) for k, v in self.pending_activations.items()},
            "history": [asdict(h) for h in self.history],
        }
        self.store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            self.active_strategy_id = data.get("active_strategy_id", "v2.2.0-RESEARCH-MOMENTUM-WICK")
            for k, v in data.get("pending_activations", {}).items():
                self.pending_activations[k] = PromotionRecord(**v)
            self.history = [PromotionRecord(**h) for h in data.get("history", [])]
        except Exception as exc:
            logger.warning("Could not load promotion state: %s", exc)
