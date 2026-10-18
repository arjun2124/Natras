"""Shadow strategy execution engine and multi-session evaluator.

Implements Section 15 of the NAT RAS Master Project Draft:
- Runs experimental variants automatically alongside the baseline.
- Tracks virtual signals, fills, trailing stops, and virtual P&L.
- Strictly isolated: NEVER submits broker orders, NEVER modifies the active baseline.
- Evaluates candidate performance across 3-5 trading sessions.
- Automatically marks weak variants as REMOVED_NO_IMPROVEMENT_3_TO_5_SESSIONS.
- Flags promising candidates as PROMOTION_CANDIDATE awaiting Telegram approval.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from natras.data.candle_engine import Candle
from natras.data.session import IST
from natras.research.walk_forward import WalkForwardBatchValidator, WalkForwardReport
from natras.risk.strategy_profiles import StrategyRiskProfile, get_default_risk_profile
from natras.strategies.base import BaseStrategy, Signal
from natras.strategies.breadth_trend_flow import BreadthInstitutionalFlowStrategy
from natras.strategies.momentum_wick import MomentumWickStrategy, SteppedTrailingStop
from natras.strategies.options_pcr_reversion import OptionsMaxPainPCRReversionStrategy
from natras.strategies.regime_momentum_wick import RegimeAdaptiveMomentumWickStrategy

logger = logging.getLogger(__name__)

SHADOW_STORE_PATH = Path("runtime_data") / "shadow" / "shadow_registry.json"

STRATEGY_CLASS_MAP: Dict[str, Any] = {
    "RegimeAdaptiveMomentumWickStrategy": RegimeAdaptiveMomentumWickStrategy,
    "OptionsMaxPainPCRReversionStrategy": OptionsMaxPainPCRReversionStrategy,
    "BreadthInstitutionalFlowStrategy": BreadthInstitutionalFlowStrategy,
    "MomentumWickStrategy": MomentumWickStrategy,
}

DEFAULT_PILLARS: List[Dict[str, Any]] = [
    {
        "name": "Candidate_1_Live_Enhancer (Regime_Adaptive_Wick)",
        "strategy_class_name": "RegimeAdaptiveMomentumWickStrategy",
        "role": "LIVE_BASELINE_ENHANCER",
        "param_overrides": {},
    },
    {
        "name": "Candidate_2_Options_Gravity (PCR_MaxPain_Reversion)",
        "strategy_class_name": "OptionsMaxPainPCRReversionStrategy",
        "role": "DIVERSE_OPTIONS_GRAVITY",
        "param_overrides": {},
    },
    {
        "name": "Candidate_3_Institutional_Flow (SectorBreadth_EMA)",
        "strategy_class_name": "BreadthInstitutionalFlowStrategy",
        "role": "DIVERSE_INSTITUTIONAL_BREADTH",
        "param_overrides": {},
    },
]


@dataclass
class ShadowTrade:
    trade_id: str
    strategy_id: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    quantity: int
    gross_pnl: float
    brokerage: float
    net_pnl: float
    exit_reason: str


@dataclass
class ShadowCandidate:
    candidate_id: str
    parent_strategy: str
    name: str
    parameters: Dict[str, Any]
    status: str  # SHADOW_LIVE, EVALUATED, PROMOTION_CANDIDATE, REMOVED_NO_IMPROVEMENT_3_TO_5_SESSIONS, INSUFFICIENT_DATA
    created_at: str
    strategy_class_name: str = "MomentumWickStrategy"
    sessions_evaluated: int = 0
    trade_count: int = 0
    win_count: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    recent_session_dates: List[str] = field(default_factory=list)
    trades: List[ShadowTrade] = field(default_factory=list)
    active_position: Optional[Dict[str, Any]] = None
    origin: str = "LIVE_MARKET_DATA"  # LIVE_MARKET_DATA or HISTORICAL_CALIBRATION
    backtest_metrics: Optional[Dict[str, Any]] = None
    backtest_status: str = "PENDING_AUDIT"  # PENDING_AUDIT, AUDITED, AUDIT_FAILED

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ShadowEngine:
    """Manages virtual execution of shadow strategy candidates with Market Intelligence integration."""

    def __init__(
        self,
        risk_profile: Optional[StrategyRiskProfile] = None,
        store_path: Path | str = SHADOW_STORE_PATH,
        auto_seed: bool = True,
    ) -> None:
        self.risk_profile = risk_profile or get_default_risk_profile()
        self.store_path = Path(store_path)
        self.candidates: Dict[str, ShadowCandidate] = {}
        self.active_strategies: Dict[str, BaseStrategy] = {}
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self._load()
        if auto_seed and not self.candidates:
            self.ensure_default_candidates()

    def get_latest_market_intelligence(self) -> Optional[Dict[str, Any]]:
        """Load freshest market intelligence report from JSON or database."""
        report_path = Path("runtime_data") / "reports" / "market_intelligence" / "latest.json"
        if report_path.exists():
            try:
                return json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def ensure_default_candidates(self) -> List[ShadowCandidate]:
        """Ensure the 3 diverse strategy pillars are registered."""
        if self.candidates:
            return list(self.candidates.values())
        added = []
        for p in DEFAULT_PILLARS:
            cand = self.register_variant(
                name=p["name"],
                param_overrides=p.get("param_overrides", {}),
                strategy_class_name=p.get("strategy_class_name", "MomentumWickStrategy"),
            )
            added.append(cand)
        return added

    def warmup_from_db(
        self,
        db_path: Path | str = Path("data") / "market.db",
        limit: int = 200,
    ) -> int:
        """Warm up strategy candle history from local sqlite database."""
        p = Path(db_path)
        if not p.exists():
            return 0
        try:
            import sqlite3
            from natras.data.candle_engine import validate_candle

            conn = sqlite3.connect(p)
            rows = conn.execute(
                "SELECT symbol, ts, open, high, low, close, volume FROM candles "
                "WHERE symbol LIKE '%NIFTY50%' ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            if not rows:
                return 0
            candles = []
            for r in reversed(rows):
                sym, ts_str, o, h, l, c, v = r
                ts = datetime.fromisoformat(ts_str)
                candles.append(validate_candle(sym, ts, o, h, l, c, v))
            count = 0
            for strat in self.active_strategies.values():
                strat.history = list(candles)
                count = len(candles)
            logger.info(
                "Warmed up %d shadow strategies with %d historical candles.",
                len(self.active_strategies),
                count,
            )
            return count
        except Exception as exc:
            logger.warning("Failed to warmup shadow engine from db: %s", exc)
            return 0

    def register_variant(
        self,
        name: str,
        param_overrides: Optional[Dict[str, Any]] = None,
        parent_id: str = "v2.2.0-RESEARCH-MOMENTUM-WICK",
        strategy_class_name: str = "MomentumWickStrategy",
    ) -> ShadowCandidate:
        """Register a new experimental variant to run in shadow mode."""
        if param_overrides is None:
            param_overrides = {}
        candidate_id = f"shadow_{uuid.uuid4().hex[:8]}"
        strat_cls = STRATEGY_CLASS_MAP.get(strategy_class_name, MomentumWickStrategy)
        strat = strat_cls(strategy_id=candidate_id, **param_overrides)
        candidate = ShadowCandidate(
            candidate_id=candidate_id,
            parent_strategy=parent_id,
            name=name,
            parameters=strat.get_status().get("parameter_defaults", {}),
            status="SHADOW_LIVE",
            created_at=datetime.now(IST).isoformat(),
            strategy_class_name=strategy_class_name,
        )
        self.candidates[candidate_id] = candidate
        self.active_strategies[candidate_id] = strat
        self._save()
        logger.info(
            "Registered shadow candidate %s (%s) [%s]",
            candidate_id,
            name,
            strategy_class_name,
        )
        return candidate

    def on_candle(self, candle: Candle, context: Optional[Dict[str, Any]] = None) -> None:
        """Evaluate incoming completed candle across all active shadow candidates."""
        if context is None:
            context = self.get_latest_market_intelligence()

        for cid, strat in self.active_strategies.items():
            candidate = self.candidates.get(cid)
            if not candidate or candidate.status != "SHADOW_LIVE":
                continue

            session_date = candle.timestamp.date().isoformat()
            if session_date not in candidate.recent_session_dates:
                candidate.recent_session_dates.append(session_date)
                candidate.sessions_evaluated = len(candidate.recent_session_dates)

            # 1. Manage active virtual position
            active_pos = self.active_positions.get(cid)
            if active_pos is not None:
                ts = active_pos["trailing_stop"]
                spot_entry = active_pos["spot_entry"]
                direction = active_pos["direction"]
                delta = 0.50

                move = (candle.close - spot_entry) * delta if direction == "CE" else (spot_entry - candle.close) * delta
                best_move = (candle.high - spot_entry) * delta if direction == "CE" else (spot_entry - candle.low) * delta
                worst_move = (candle.low - spot_entry) * delta if direction == "CE" else (spot_entry - candle.high) * delta

                ts.update(best_move)
                if candidate.active_position:
                    candidate.active_position["peak_fav_pp"] = ts.peak_fav_pp
                    candidate.active_position["current_stop_pp"] = ts.current_stop_pp

                is_stopped = ts.is_stopped_out(worst_move)
                is_eod = strat.is_eod_square_off_time(candle.timestamp) if hasattr(strat, "is_eod_square_off_time") else False

                if is_stopped or is_eod:
                    exit_reason = "EOD_SQUARE_OFF" if is_eod and not is_stopped else "TRAILING_STOP_BREACH"
                    exit_points = ts.current_stop_pp if is_stopped else move
                    quoted_exit = active_pos["quoted_entry"] + exit_points
                    fill_exit = quoted_exit - self.risk_profile.exit_slippage

                    pnl_pts = fill_exit - active_pos["fill_entry"]
                    gross = pnl_pts * active_pos["quantity"]
                    brokerage = self.risk_profile.brokerage_per_order * 2.0
                    net = round(gross - brokerage, 2)

                    trade = ShadowTrade(
                        trade_id=active_pos["trade_id"],
                        strategy_id=cid,
                        direction=direction,
                        entry_ts=active_pos["entry_ts"],
                        entry_price=active_pos["fill_entry"],
                        exit_ts=candle.timestamp.isoformat(),
                        exit_price=round(fill_exit, 2),
                        quantity=active_pos["quantity"],
                        gross_pnl=round(gross, 2),
                        brokerage=brokerage,
                        net_pnl=net,
                        exit_reason=exit_reason,
                    )
                    candidate.trades.append(trade)
                    candidate.trade_count += 1
                    if net > 0:
                        candidate.win_count += 1
                    candidate.gross_pnl = round(candidate.gross_pnl + gross, 2)
                    candidate.net_pnl = round(candidate.net_pnl + net, 2)

                    candidate.active_position = None
                    del self.active_positions[cid]
                    self._evaluate_lifecycle(candidate)
                    self._save()
                    logger.info(
                        "Shadow candidate %s (%s) closed virtual %s trade: Net PnL=%.2f (%s)",
                        cid,
                        candidate.name,
                        direction,
                        net,
                        exit_reason,
                    )
                    continue

            # 2. Check for new virtual signal
            if active_pos is None:
                signals = strat.on_candle(candle, context=context)
                if signals:
                    sig = signals[0]
                    direction = sig.metadata.get("instrument_type", "CE")
                    quoted_entry = 100.0
                    fill_entry = quoted_entry + self.risk_profile.entry_slippage
                    if hasattr(strat, "create_trailing_stop"):
                        try:
                            ts = strat.create_trailing_stop(context=context)
                        except TypeError:
                            ts = strat.create_trailing_stop()
                    else:
                        ts = SteppedTrailingStop()

                    pos = {
                        "trade_id": f"st_{uuid.uuid4().hex[:8]}",
                        "direction": direction,
                        "spot_entry": candle.close,
                        "quoted_entry": quoted_entry,
                        "fill_entry": fill_entry,
                        "entry_ts": candle.timestamp.isoformat(),
                        "quantity": self.risk_profile.lot_size_units,
                        "trailing_stop": ts,
                    }
                    self.active_positions[cid] = pos
                    candidate.active_position = {
                        "trade_id": pos["trade_id"],
                        "direction": direction,
                        "spot_entry": candle.close,
                        "quoted_entry": quoted_entry,
                        "fill_entry": fill_entry,
                        "entry_ts": pos["entry_ts"],
                        "quantity": pos["quantity"],
                        "peak_fav_pp": ts.peak_fav_pp,
                        "current_stop_pp": ts.current_stop_pp,
                    }
                    self._save()
                    logger.info(
                        "Shadow candidate %s (%s) opened virtual %s trade at spot=%.2f",
                        cid,
                        candidate.name,
                        direction,
                        candle.close,
                    )

    def _evaluate_lifecycle(self, candidate: ShadowCandidate) -> None:
        """Evaluate candidate against 3-5 sessions removal or promotion rule."""
        if candidate.sessions_evaluated < 3:
            return

        # Check opportunity sample size
        if candidate.trade_count < 2:
            candidate.status = "INSUFFICIENT_DATA"
            return

        # Calculate profit factor
        wins = sum(t.net_pnl for t in candidate.trades if t.net_pnl > 0)
        losses = abs(sum(t.net_pnl for t in candidate.trades if t.net_pnl < 0))
        candidate.profit_factor = round(wins / losses, 2) if losses > 0 else (99.0 if wins > 0 else 0.0)

        # Removal Rule (Section 15): If no improvement after 3-5 sessions, remove variant
        if candidate.net_pnl <= 0.0 or candidate.profit_factor < 1.0:
            if candidate.sessions_evaluated >= 3:
                candidate.status = "REMOVED_NO_IMPROVEMENT_3_TO_5_SESSIONS"
                logger.info(
                    "Shadow candidate %s removed after %d sessions (Net PnL: %.2f)",
                    candidate.candidate_id,
                    candidate.sessions_evaluated,
                    candidate.net_pnl,
                )
        else:
            # Promising variant: qualify as promotion candidate
            if candidate.profit_factor >= 1.5 and candidate.trade_count >= 3:
                candidate.status = "PROMOTION_CANDIDATE"
                logger.info(
                    "Shadow candidate %s qualified as PROMOTION_CANDIDATE (Net PnL: %.2f, PF: %.2f)",
                    candidate.candidate_id,
                    candidate.net_pnl,
                    candidate.profit_factor,
                )

    def run_walkforward_audit(
        self,
        candidate_id: str,
        db_path: Path | str = Path("data") / "market.db",
    ) -> WalkForwardReport:
        """Execute historical walk-forward batch validation (6-month calibration + out-of-sample batches)."""
        candidate = self.candidates.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found in shadow registry")
        strat_cls = STRATEGY_CLASS_MAP.get(
            candidate.strategy_class_name, MomentumWickStrategy
        )
        strategy = strat_cls(strategy_id=candidate_id, **candidate.parameters)
        validator = WalkForwardBatchValidator(db_path=db_path)
        report = validator.run(strategy)
        candidate.backtest_metrics = {
            "total_trades": report.total_trades,
            "win_rate": report.total_win_rate,
            "total_net_pnl": report.total_net_pnl,
            "profit_factor": report.overall_profit_factor,
            "consistency_pct": report.consistency_percentage,
            "profitable_batches": report.profitable_batches,
            "total_batches": report.batches_evaluated,
            "certification_status": report.certification_status,
        }
        candidate.backtest_status = "AUDITED"
        self._evaluate_lifecycle(candidate)
        self._save()
        return report

    def run_background_audit_async(
        self,
        candidate_id: str,
        db_path: Path | str = Path("data") / "market.db",
        on_complete: Optional[Any] = None,
    ) -> Any:
        """Run historical walk-forward audit in background daemon thread without blocking live execution."""
        import threading

        def _target():
            try:
                rep = self.run_walkforward_audit(candidate_id, db_path=db_path)
                if on_complete:
                    try:
                        on_complete(rep)
                    except Exception:
                        pass
            except Exception as exc:
                logger.error("Async background audit failed for %s: %s", candidate_id, exc)

        t = threading.Thread(target=_target, name=f"AuditThread_{candidate_id}", daemon=True)
        t.start()
        return t

    def _save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {cid: c.to_dict() for cid, c in self.candidates.items()}
        self.store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            known_fields = set(ShadowCandidate.__dataclass_fields__.keys())
            for cid, d in data.items():
                trades = [ShadowTrade(**t) for t in d.get("trades", [])]
                d["trades"] = trades
                filtered_d = {k: v for k, v in d.items() if k in known_fields}
                cand = ShadowCandidate(**filtered_d)
                self.candidates[cid] = cand
                if cand.status == "SHADOW_LIVE":
                    strat_cls = STRATEGY_CLASS_MAP.get(
                        cand.strategy_class_name, MomentumWickStrategy
                    )
                    strat = strat_cls(strategy_id=cid, **cand.parameters)
                    self.active_strategies[cid] = strat
                    if cand.active_position:
                        if hasattr(strat, "create_trailing_stop"):
                            ts = strat.create_trailing_stop()
                        else:
                            ts = SteppedTrailingStop()
                        ts.peak_fav_pp = cand.active_position.get("peak_fav_pp", 0.0)
                        ts.current_stop_pp = cand.active_position.get(
                            "current_stop_pp", -abs(getattr(strat, "initial_stop_pp", 15.0))
                        )
                        self.active_positions[cid] = {
                            **cand.active_position,
                            "trailing_stop": ts,
                        }
        except Exception as exc:
            logger.warning("Could not load shadow registry: %s", exc)
