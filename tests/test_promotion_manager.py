"""Unit tests for PromotionManager next-session activation and rollback."""

from datetime import datetime

import pytest

from natras.promotion.promotion_manager import PromotionManager
from natras.shadow.shadow_engine import ShadowCandidate


def test_promotion_manager_approval_and_rollback(tmp_path):
    store_file = tmp_path / "promotion_state.json"
    mgr = PromotionManager(store_path=store_file)

    candidate = ShadowCandidate(
        candidate_id="shadow_abc123",
        parent_strategy="v2.2.0-RESEARCH-MOMENTUM-WICK",
        name="Candidate_Test",
        parameters={"min_body_ratio": 0.50},
        status="PROMOTION_CANDIDATE",
        created_at="2026-10-14T09:00:00",
        sessions_evaluated=4,
        trade_count=12,
        win_count=8,
        net_pnl=4250.0,
        profit_factor=2.4,
    )

    # 1. Report format
    report = mgr.create_candidate_report(candidate)
    assert "shadow_abc123" in report
    assert "/approve shadow_abc123" in report

    # 2. Approve candidate for next session
    rec = mgr.approve_candidate(candidate, approved_by="TraderArjun")
    assert rec.status == "PENDING_ACTIVATION"
    assert mgr.active_strategy_id == "v2.2.0-RESEARCH-MOMENTUM-WICK"  # Still baseline mid-session!

    # 3. Next session begins -> activate
    activated = mgr.activate_scheduled_for_session()
    assert activated == "shadow_abc123"
    assert mgr.active_strategy_id == "shadow_abc123"

    # 4. Rollback
    rolled = mgr.rollback()
    assert rolled == "v2.2.0-RESEARCH-MOMENTUM-WICK"
    assert mgr.active_strategy_id == "v2.2.0-RESEARCH-MOMENTUM-WICK"
